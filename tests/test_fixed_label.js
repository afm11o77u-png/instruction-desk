const assert = require('assert')
const {
  FIXED_LABEL_CSS,
  FIXED_LABEL_FORMATS,
  listFixedLabelFormats,
  getFixedLabelFormat,
  normalizeFixedLabelSheetCount,
  validateFixedLabelPrintInput,
  fixedLabelPagesHtml,
  fixedLabelPreviewHtml,
  fixedLabelIsPrintOnly,
  fixedLabelMutatesOrders,
} = require('../fixed_label.js')

assert.strictEqual(fixedLabelIsPrintOnly(), true)
assert.strictEqual(fixedLabelMutatesOrders(), false)

const formats = listFixedLabelFormats()
assert.ok(formats.length >= 1)
assert.strictEqual(formats[0].id, 'daruma-zenibako')
assert.strictEqual(formats[0].name, 'だるま食品銭函工場')
assert.ok(getFixedLabelFormat('daruma-zenibako'))
assert.strictEqual(getFixedLabelFormat('missing'), null)

assert.strictEqual(normalizeFixedLabelSheetCount('1'), 1)
assert.strictEqual(normalizeFixedLabelSheetCount(3), 3)
assert.strictEqual(normalizeFixedLabelSheetCount(10), 10)
assert.strictEqual(normalizeFixedLabelSheetCount(0), null)
assert.strictEqual(normalizeFixedLabelSheetCount(-1), null)
assert.strictEqual(normalizeFixedLabelSheetCount(1.5), null)
assert.strictEqual(normalizeFixedLabelSheetCount(''), null)
assert.strictEqual(normalizeFixedLabelSheetCount(101), null)

assert.strictEqual(validateFixedLabelPrintInput({ formatId: '', sheetCount: 1 }).ok, false)
assert.strictEqual(validateFixedLabelPrintInput({ formatId: 'daruma-zenibako', sheetCount: 0 }).ok, false)
const ok = validateFixedLabelPrintInput({ formatId: 'daruma-zenibako', sheetCount: 3 })
assert.strictEqual(ok.ok, true)
assert.strictEqual(ok.sheetCount, 3)

function count(html, re) {
  return (html.match(re) || []).length
}

for (const sheets of [1, 3, 10]) {
  const html = fixedLabelPreviewHtml('daruma-zenibako', sheets)
  assert.strictEqual(count(html, /class="page"/g), sheets, `pages for ${sheets}`)
  assert.strictEqual(count(html, /class="cell"/g), sheets * 4, `cells for ${sheets}`)
  assert.strictEqual(count(html, /だるま食品銭函工場/g), sheets * 4)
  assert.ok(html.includes('製造１課'))
  assert.ok(html.includes('設備保全課'))
  assert.ok(html.includes('工場長'))
  assert.ok(html.includes('NO.'))
  assert.ok(html.includes('様'))
  assert.ok(html.includes('新規　交換　その他'))
  assert.ok(!html.includes('api('))
}

assert.ok(FIXED_LABEL_CSS.includes('@page{size:A4 landscape;margin:0}'))
assert.ok(FIXED_LABEL_CSS.includes('297mm'))
assert.ok(FIXED_LABEL_CSS.includes('210mm'))
assert.ok(FIXED_LABEL_CSS.includes('148.5mm'))
assert.ok(FIXED_LABEL_CSS.includes('105mm'))
assert.ok(FIXED_LABEL_CSS.includes('dashed'))

const pages = fixedLabelPagesHtml('daruma-zenibako', 1)
assert.strictEqual(count(pages, /class="page"/g), 1)
assert.strictEqual(count(pages, /class="cell"/g), 4)

assert.ok(Array.isArray(FIXED_LABEL_FORMATS))
assert.strictEqual(typeof FIXED_LABEL_FORMATS[0].buildCellHtml, 'function')

console.log('ok')
