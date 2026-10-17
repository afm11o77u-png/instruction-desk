const assert = require('assert')
const fs = require('fs')
const path = require('path')
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

const ROOT = path.join(__dirname, '..')

assert.strictEqual(fixedLabelIsPrintOnly(), true)
assert.strictEqual(fixedLabelMutatesOrders(), false)

const formats = listFixedLabelFormats()
assert.ok(formats.length >= 5)
assert.strictEqual(formats[0].id, 'daruma-zenibako')
assert.strictEqual(formats[0].name, 'だるま食品銭函工場')
assert.ok(getFixedLabelFormat('daruma-zenibako'))
assert.strictEqual(getFixedLabelFormat('missing'), null)

const expectedImageFormats = [
  { id: 'nittetuft-2239', name: '日鉄FT', file: 'fixed-label/2239-nittetuft.jpg' },
  { id: 'rose-delica-2-2240', name: 'ローズデリカ２課', file: 'fixed-label/2240-rose-delica-2.jpg' },
  { id: 'rose-delica-1-2241', name: 'ローズデリカ１課', file: 'fixed-label/2241-rose-delica-1.jpg' },
  { id: 'ishiya-2242', name: '石屋商事', file: 'fixed-label/2242-ishiya.jpg' },
]
for (const exp of expectedImageFormats) {
  const f = getFixedLabelFormat(exp.id)
  assert.ok(f, exp.id)
  assert.strictEqual(f.name, exp.name)
  assert.strictEqual(f.kind, 'page-image')
  assert.ok(fs.existsSync(path.join(ROOT, exp.file)), exp.file)
  assert.ok(fs.existsSync(path.join(ROOT, exp.file.replace(/\.jpg$/, '.pdf'))), exp.file + ' pdf')
}
assert.strictEqual(getFixedLabelFormat('ishiya-2242').sourcePageRotate, 180)

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

for (const exp of expectedImageFormats) {
  for (const sheets of [1, 3]) {
    const html = fixedLabelPreviewHtml(exp.id, sheets)
    assert.strictEqual(count(html, /class="page page-image"/g), sheets, `${exp.id} pages ${sheets}`)
    assert.strictEqual(count(html, /class="cell"/g), 0, `${exp.id} must not split cells`)
    assert.strictEqual(count(html, /class="sheetimg"/g), sheets)
    assert.ok(html.includes(exp.file.replace(/^fixed-label\//, '/fixed-label/')))
    assert.ok(!html.includes('transform:rotate'), `${exp.id}: no CSS re-rotation`)
    assert.ok(!html.includes('api('))
  }
  // assetBase for headless/file:// paths
  const withBase = fixedLabelPreviewHtml(exp.id, 1, null, 'file:///tmp/assets/')
  assert.ok(withBase.includes(`file:///tmp/assets/${exp.file}`))
}

assert.ok(FIXED_LABEL_CSS.includes('@page{size:A4 landscape;margin:0}'))
assert.ok(FIXED_LABEL_CSS.includes('297mm'))
assert.ok(FIXED_LABEL_CSS.includes('210mm'))
assert.ok(FIXED_LABEL_CSS.includes('148.5mm'))
assert.ok(FIXED_LABEL_CSS.includes('105mm'))
assert.ok(FIXED_LABEL_CSS.includes('dashed'))
assert.ok(FIXED_LABEL_CSS.includes('.page.page-image'))
assert.ok(FIXED_LABEL_CSS.includes('object-fit:contain'))

const pages = fixedLabelPagesHtml('daruma-zenibako', 1)
assert.strictEqual(count(pages, /class="page"/g), 1)
assert.strictEqual(count(pages, /class="cell"/g), 4)

assert.ok(Array.isArray(FIXED_LABEL_FORMATS))
assert.strictEqual(typeof FIXED_LABEL_FORMATS[0].buildCellHtml, 'function')
assert.strictEqual(FIXED_LABEL_FORMATS.filter((f) => f.kind === 'page-image').length, 4)

console.log('ok')
