'use strict'
const assert = require('assert')
const path = require('path')
const {
  LABEL_PASTE_KEYS,
  LABEL_PASTE_MAX_ROWS,
  parseLabelClipboard,
  planLabelPaste,
  labelPasteNeedsConfirm,
} = require(path.join(__dirname, '../label_paste.js'))

assert.deepStrictEqual(LABEL_PASTE_KEYS, ['customer', 'management', 'department', 'employee', 'request'])
assert.strictEqual(LABEL_PASTE_MAX_ROWS, 40)

// Single-cell paste (Excel trailing newline) stays native.
assert.strictEqual(parseLabelClipboard('01234\n'), null)
assert.strictEqual(parseLabelClipboard('だけ'), null)
assert.strictEqual(parseLabelClipboard(''), null)

// 4×5 TSV from Excel
const fourByFive = [
  '顧客A\t0123\t部署1\t山田\t新規',
  '顧客B\t\t部署2\t佐藤\tサイズ交換',
  '顧客C\t0045\t\t鈴木\t消耗・破損交換',
  '顧客D\t100\t部署4\t\t追加・補充',
].join('\n') + '\n'
const grid = parseLabelClipboard(fourByFive)
assert.strictEqual(grid.length, 4)
assert.strictEqual(grid[0].length, 5)
assert.strictEqual(grid[0][1], '0123', 'leading zero preserved')
assert.strictEqual(grid[1][1], '', 'blank management stays a column')
assert.strictEqual(grid[2][2], '', 'blank department stays a column')
assert.strictEqual(grid[3][3], '', 'blank employee stays a column')

const planned = planLabelPaste({ rows: grid, originRow: 0, originCol: 0, cardCount: 4 })
assert.strictEqual(planned.ok, true)
assert.strictEqual(planned.rowCount, 4)
assert.strictEqual(planned.colCount, 5)
assert.strictEqual(planned.needCards, 4)
assert.strictEqual(planned.updates.length, 20)
assert.deepStrictEqual(
  planned.updates.filter(u => u.row === 0).map(u => [u.key, u.value]),
  [
    ['customer', '顧客A'],
    ['management', '0123'],
    ['department', '部署1'],
    ['employee', '山田'],
    ['request', '新規'],
  ]
)
assert.deepStrictEqual(
  planned.updates.filter(u => u.key === 'management').map(u => u.value),
  ['0123', '', '0045', '100']
)

// Origin mid-field: remaining columns only
const fromDept = planLabelPaste({
  rows: parseLabelClipboard('部署X\t名前X\n部署Y\t名前Y\n'),
  originRow: 1,
  originCol: 2,
  cardCount: 4,
})
assert.strictEqual(fromDept.ok, true)
assert.deepStrictEqual(
  fromDept.updates.map(u => [u.row, u.key, u.value]),
  [
    [1, 'department', '部署X'],
    [1, 'employee', '名前X'],
    [2, 'department', '部署Y'],
    [2, 'employee', '名前Y'],
  ]
)

// Too many columns: reject, no silent truncate
const tooWide = planLabelPaste({
  rows: parseLabelClipboard('a\tb\tc\n'),
  originRow: 0,
  originCol: 3,
  cardCount: 4,
})
assert.strictEqual(tooWide.ok, false)
assert.match(tooWide.message, /列数が多すぎます/)
assert.match(tooWide.message, /切り捨てずに中止/)

// Too many rows from origin: reject
const tooTall = planLabelPaste({
  rows: parseLabelClipboard(['r1', 'r2', 'r3', 'r4', 'r5'].join('\n') + '\n'),
  originRow: 0,
  originCol: 0,
  cardCount: 4,
  maxRows: 4,
})
assert.strictEqual(tooTall.ok, false)
assert.match(tooTall.message, /行数が多すぎます/)

// Auto-expand within 40
const expand = planLabelPaste({
  rows: parseLabelClipboard(['a', 'b', 'c', 'd', 'e'].join('\n') + '\n'),
  originRow: 0,
  originCol: 0,
  cardCount: 4,
})
assert.strictEqual(expand.ok, true)
assert.strictEqual(expand.needCards, 8)

// Over capacity 40 from near the end
const over40 = planLabelPaste({
  rows: parseLabelClipboard(['a', 'b', 'c'].join('\n') + '\n'),
  originRow: 38,
  originCol: 0,
  cardCount: 40,
})
assert.strictEqual(over40.ok, false)
assert.match(over40.message, /行数が多すぎます/)

// Invalid 受付内容
const badRequest = planLabelPaste({
  rows: parseLabelClipboard('顧客\t1\t部\t名\t存在しない\n'),
  originRow: 0,
  originCol: 0,
  cardCount: 4,
})
assert.strictEqual(badRequest.ok, false)
assert.match(badRequest.message, /受付内容/)

// Overwrite confirm: filled inputs only (not request select)
const sample = planLabelPaste({
  rows: parseLabelClipboard('新顧客\t0999\t\t\t\n'),
  originRow: 0,
  originCol: 0,
  cardCount: 4,
})
assert.strictEqual(sample.ok, true)
const empty = {}
assert.strictEqual(
  labelPasteNeedsConfirm(sample.updates, (row, key) => empty[`${row}.${key}`] || ''),
  false
)
assert.strictEqual(
  labelPasteNeedsConfirm(sample.updates, (row, key) => (key === 'customer' && row === 0 ? '既存' : '')),
  true
)
assert.strictEqual(
  labelPasteNeedsConfirm(sample.updates, (row, key) => (key === 'request' ? '新規' : '')),
  false,
  'request select alone does not force confirm'
)

// CRLF from Excel
const crlf = parseLabelClipboard('A\tB\r\nC\tD\r\n')
assert.deepStrictEqual(crlf, [
  ['A', 'B'],
  ['C', 'D'],
])

const {
  labelCardHasAddress,
  planLabelRequestBatch,
  LABEL_REQUEST_OPTIONS,
} = require(path.join(__dirname, '../label_paste.js'))

assert.strictEqual(labelCardHasAddress({ customer: 'A' }), true)
assert.strictEqual(labelCardHasAddress({ management: '01' }), true)
assert.strictEqual(labelCardHasAddress({ department: '部' }), true)
assert.strictEqual(labelCardHasAddress({ employee: '山田' }), true)
assert.strictEqual(labelCardHasAddress({ request: '新規', customer: '', management: '', department: '', employee: '' }), false)
assert.strictEqual(labelCardHasAddress({}), false)

const batchEmpty = planLabelRequestBatch({ request: '', slots: [{ customer: 'A' }] })
assert.strictEqual(batchEmpty.ok, false)
assert.strictEqual(batchEmpty.reason, 'empty')
assert.match(batchEmpty.message, /受付内容を選んで/)

const batchNone = planLabelRequestBatch({
  request: '新規',
  slots: [
    { customer: '', management: '', department: '', employee: '', request: '消耗・破損交換' },
    { customer: '  ', request: '新規' },
  ],
})
assert.strictEqual(batchNone.ok, false)
assert.strictEqual(batchNone.reason, 'none')
assert.match(batchNone.message, /適用できる宛名がありません/)

const batchOk = planLabelRequestBatch({
  request: '追加・補充',
  slots: [
    { customer: '顧客A', management: '01', department: '部', employee: '山田', request: '新規' },
    { customer: '', management: '', department: '', employee: '', request: '消耗・破損交換' },
    { customer: '顧客C', management: '', department: '', employee: '佐藤', request: '復職' },
    { customer: '', management: '99', department: '', employee: '', request: 'その他' },
  ],
})
assert.strictEqual(batchOk.ok, true)
assert.strictEqual(batchOk.value, '追加・補充')
assert.deepStrictEqual(batchOk.targets, [0, 2, 3])
assert.strictEqual(batchOk.confirmMessage, "3件の受付内容を『追加・補充』に変更します。よろしいですか？")
assert.ok(LABEL_REQUEST_OPTIONS.includes('追加・補充'))

const batchBad = planLabelRequestBatch({ request: '存在しない', slots: [{ customer: 'A' }] })
assert.strictEqual(batchBad.ok, false)
assert.strictEqual(batchBad.reason, 'invalid')

console.log('All label paste unit tests passed')
