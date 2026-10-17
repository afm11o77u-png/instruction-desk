const assert = require('assert')
const {
  HANDWRITE_LABEL_CSS,
  normalizeHandwriteCustomerName,
  normalizeHandwriteSheetCount,
  validateHandwriteLabelPrintInput,
  handwriteCustomerFontSizePt,
  handwriteLabelPagesHtml,
  handwriteLabelPreviewHtml,
  handwriteLabelIsPrintOnly,
  handwriteLabelMutatesOrders,
} = require('../handwrite_label.js')

assert.strictEqual(handwriteLabelIsPrintOnly(), true)
assert.strictEqual(handwriteLabelMutatesOrders(), false)

assert.strictEqual(normalizeHandwriteCustomerName('  日総　らくら砂川  '), '日総 らくら砂川')
assert.strictEqual(normalizeHandwriteCustomerName(''), '')
assert.strictEqual(normalizeHandwriteCustomerName(null), '')

assert.strictEqual(normalizeHandwriteSheetCount('1'), 1)
assert.strictEqual(normalizeHandwriteSheetCount(3), 3)
assert.strictEqual(normalizeHandwriteSheetCount(10), 10)
assert.strictEqual(normalizeHandwriteSheetCount(0), null)
assert.strictEqual(normalizeHandwriteSheetCount(-1), null)
assert.strictEqual(normalizeHandwriteSheetCount(1.5), null)
assert.strictEqual(normalizeHandwriteSheetCount(''), null)
assert.strictEqual(normalizeHandwriteSheetCount(101), null)

assert.strictEqual(validateHandwriteLabelPrintInput({ customerName: '', sheetCount: 1 }).ok, false)
assert.strictEqual(
  validateHandwriteLabelPrintInput({ customerName: '', sheetCount: 1 }).error,
  '顧客名を入力してください。'
)
assert.strictEqual(validateHandwriteLabelPrintInput({ customerName: 'テスト', sheetCount: 0 }).ok, false)
assert.strictEqual(validateHandwriteLabelPrintInput({ customerName: 'テスト', sheetCount: -1 }).ok, false)
assert.strictEqual(validateHandwriteLabelPrintInput({ customerName: 'テスト', sheetCount: 1.5 }).ok, false)
assert.strictEqual(validateHandwriteLabelPrintInput({ customerName: 'テスト', sheetCount: '' }).ok, false)
assert.ok(
  validateHandwriteLabelPrintInput({ customerName: 'テスト', sheetCount: 0 }).error.includes('枚数')
)

const ok = validateHandwriteLabelPrintInput({ customerName: ' 日総 らくら砂川 ', sheetCount: 3 })
assert.strictEqual(ok.ok, true)
assert.strictEqual(ok.customerName, '日総 らくら砂川')
assert.strictEqual(ok.sheetCount, 3)

assert.ok(handwriteCustomerFontSizePt('短い') >= handwriteCustomerFontSizePt('とても長い顧客名のサンプル文字列です'))
assert.ok(handwriteCustomerFontSizePt('日総 らくら砂川') >= 20)

function count(html, re) {
  return (html.match(re) || []).length
}

for (const sheets of [1, 3, 10]) {
  const html = handwriteLabelPreviewHtml('日総 らくら砂川', sheets)
  assert.strictEqual(count(html, /class="page"/g), sheets, `pages for ${sheets}`)
  assert.strictEqual(count(html, /class="cell"/g), sheets * 4, `cells for ${sheets}`)
  assert.strictEqual(count(html, /日総 らくら砂川/g), sheets * 4)
  assert.strictEqual(count(html, /class="sama"/g), sheets * 4)
  assert.strictEqual(count(html, />様</g), sheets * 4)
  assert.ok(html.includes('class="guide"'))
  assert.ok(!html.includes('api('))
  assert.ok(!html.includes('transform:rotate'), 'no CSS re-rotation')
}

const pages = handwriteLabelPagesHtml('サンプル商事', 1)
assert.strictEqual(count(pages, /class="page"/g), 1)
assert.strictEqual(count(pages, /class="cell"/g), 4)
assert.ok(!pages.includes('個人名'))

assert.ok(HANDWRITE_LABEL_CSS.includes('@page{size:A4 landscape;margin:0}'))
assert.ok(HANDWRITE_LABEL_CSS.includes('297mm'))
assert.ok(HANDWRITE_LABEL_CSS.includes('210mm'))
assert.ok(HANDWRITE_LABEL_CSS.includes('148.5mm'))
assert.ok(HANDWRITE_LABEL_CSS.includes('105mm'))
assert.ok(HANDWRITE_LABEL_CSS.includes('solid'))

assert.throws(() => handwriteLabelPagesHtml('', 1), /顧客名/)
assert.throws(() => handwriteLabelPagesHtml('x', 0), /枚数/)

console.log('ok')
