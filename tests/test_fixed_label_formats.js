'use strict'
/* 登録した定型書式のプレビュー。組み込みのだるま・石屋はここでも回帰する。 */
const assert = require('assert')
const f = require('../fixed_label.js')

function count(html, re) {
  return (html.match(re) || []).length
}

assert.strictEqual(f.fixedLabelIsPrintOnly(), true)
assert.strictEqual(f.fixedLabelMutatesOrders(), false)
assert.strictEqual(f.listFixedLabelFormats()[0].id, 'daruma-zenibako')
assert.strictEqual(f.getFixedLabelFormat('ishiya-2242').kind, 'page-image')
assert.strictEqual(f.getFixedLabelFormat('ishiya-2242').name, '石屋商事')
assert.deepStrictEqual(f.fixedLabelBuiltinNames(), [
  'だるま食品銭函工場',
  '日鉄FT',
  'ローズデリカ２課',
  'ローズデリカ１課',
  '石屋商事',
  'ひまわり1',
  'ひまわり2',
  'ひまわり3',
  'ひまわり4',
])

const builtin = f.fixedLabelPreviewHtml('daruma-zenibako', 3)
assert.strictEqual(count(builtin, /class="page"/g), 3)
assert.strictEqual(count(builtin, /class="cell"/g), 12)
assert.ok(builtin.includes('製造１課'))
assert.ok(builtin.includes('工場長'))
assert.ok(builtin.includes('新規　交換　その他'))
assert.ok(!builtin.includes('class="cell tiers"'))
assert.ok(!builtin.includes('class="cell depts"'))
const ishiya = f.fixedLabelPreviewHtml('ishiya-2242', 1)
assert.strictEqual(count(ishiya, /class="page page-image"/g), 1)
assert.ok(ishiya.includes('/fixed-label/2242-ishiya.jpg'))
assert.strictEqual(count(ishiya, /class="cell/g), 0)

const tiers = {
  name: '長尾様行',
  kind: 'tiers',
  payload: { lines: ['石屋商事', '長尾様行', '納品日　月　日'] },
}
for (const sheets of [1, 3]) {
  const html = f.savedFixedLabelPreviewHtml(tiers, sheets)
  assert.strictEqual(count(html, /class="page"/g), sheets, `tier pages ${sheets}`)
  assert.strictEqual(count(html, /class="cell tiers"/g), sheets * 4, `tier cells ${sheets}`)
  assert.strictEqual(count(html, /石屋商事/g), sheets * 4)
  assert.strictEqual(count(html, /長尾様行/g), sheets * 4)
  assert.strictEqual(count(html, /納品日　月　日/g), sheets * 4)
  assert.ok(html.includes('white-space:pre-wrap'))
  assert.ok(!html.includes('新規　交換　その他'), '3段には新規交換を足さない')
  assert.ok(!html.includes('class="no"'), '3段にはNO.を足さない')
  assert.ok(html.includes('@page{size:A4 landscape;margin:0}'))
  assert.ok(html.includes('dashed'))
}

const evilTier = f.savedFixedLabelPreviewHtml({
  name: '危険な3段',
  kind: 'tiers',
  payload: { lines: ['<script>alert(1)</script>', 'A&B', '納品日　月　日'] },
}, 1)
assert.ok(!evilTier.includes('<script>'))
assert.ok(evilTier.includes('&lt;script&gt;alert(1)&lt;/script&gt;'))
assert.ok(evilTier.includes('A&amp;B'))
assert.ok(!evilTier.includes('<b>'))

const departments = ['製造１課', '製造２課', '衛生']
const deptRecord = {
  name: '銭函チェック',
  kind: 'departments',
  payload: { customer: 'だるま食品銭函工場', departments },
}
for (const sheets of [1, 3]) {
  const html = f.savedFixedLabelPreviewHtml(deptRecord, sheets)
  assert.strictEqual(count(html, /class="page"/g), sheets)
  assert.strictEqual(count(html, /class="cell depts"/g), sheets * 4)
  assert.strictEqual(count(html, /だるま食品銭函工場/g), sheets * 4)
  assert.strictEqual(count(html, /□/g), departments.length * 4 * sheets)
  assert.ok(html.includes('>NO.</span>'))
  assert.ok(html.includes('class="name-row"'))
  assert.ok(html.includes('>様</div>'))
  assert.ok(html.includes('新規　交換　その他'))
  assert.ok(html.indexOf('class="title"') < html.indexOf('class="checks'))
  assert.ok(html.indexOf('class="checks') < html.indexOf('class="no"'))
  assert.ok(html.indexOf('class="no"') < html.indexOf('class="name-row"'))
  assert.ok(html.indexOf('class="name-row"') < html.indexOf('class="footer"'))
  assert.strictEqual(count(html, /class="checkrow four"/g), sheets * 4)
}
const wide = f.savedFixedLabelPreviewHtml({
  name: '長い部署',
  kind: 'departments',
  payload: { customer: '顧客', departments: ['インフォメーション', '品質管理室'] },
}, 1)
assert.ok(wide.includes('class="checkrow cols3"'))
assert.strictEqual(count(wide, /□/g), 8)

const evilDept = f.savedFixedLabelPreviewHtml({
  name: '危険な部署',
  kind: 'departments',
  payload: { customer: '<img src=x onerror=alert(1)>', departments: ['A&B', '"><script>'] },
}, 1)
assert.ok(!evilDept.includes('<img'))
assert.ok(!evilDept.includes('<script>'))
assert.ok(evilDept.includes('&lt;img src=x onerror=alert(1)&gt;'))
assert.ok(evilDept.includes('A&amp;B'))
assert.ok(evilDept.includes('"&gt;&lt;script&gt;'))

const many = Array.from({ length: 24 }, (_, i) => `課${i + 1}`)
const manyHtml = f.savedFixedLabelPreviewHtml({
  name: '多い部署',
  kind: 'departments',
  payload: { customer: '顧客', departments: many },
}, 1)
assert.strictEqual(count(manyHtml, /□/g), 24 * 4)
assert.ok(manyHtml.includes('新規　交換　その他'))
assert.ok(manyHtml.includes('class="checks tight"'))

assert.strictEqual(f.validateSavedFixedLabelPrintInput(deptRecord, 0).ok, false)
assert.strictEqual(f.validateSavedFixedLabelPrintInput(deptRecord, 101).ok, false)
assert.strictEqual(f.validateSavedFixedLabelPrintInput(deptRecord, 2).sheetCount, 2)

const draft = f.validateFixedLabelDraft({
  name: '  長尾様行  ',
  kind: 'tiers',
  payload: { lines: [' 石屋商事 ', '長尾様行', '納品日　月　日'] },
})
assert.strictEqual(draft.ok, true)
assert.strictEqual(draft.name, '長尾様行')
assert.strictEqual(draft.payload.lines[0], '石屋商事')
assert.strictEqual(draft.payload.lines[2], '納品日　月　日')
assert.strictEqual(f.validateFixedLabelDraft({
  name: '石屋商事',
  kind: 'tiers',
  payload: { lines: ['あ', 'い', 'う'] },
}).ok, false)
assert.strictEqual(f.validateFixedLabelDraft({
  name: '空部署',
  kind: 'departments',
  payload: { customer: '顧客', departments: ['製造', '   '] },
}).ok, false)
const cleaned = f.validateFixedLabelDraft({
  name: '制御',
  kind: 'departments',
  payload: { customer: '顧\n客', departments: ['製\u0001造\u200b'] },
})
assert.strictEqual(cleaned.ok, true)
assert.strictEqual(cleaned.payload.customer, '顧客')
assert.strictEqual(cleaned.payload.departments[0], '製造')

assert.strictEqual(f.departmentRowChange(0, 'add').count, 1)
assert.strictEqual(f.departmentRowChange(3, 'add').count, 4)
assert.strictEqual(f.departmentRowChange(23, 'add').ok, true)
assert.strictEqual(f.departmentRowChange(23, 'add').count, 24)
assert.strictEqual(f.departmentRowChange(24, 'add').ok, false)
assert.strictEqual(f.departmentRowChange(1, 'remove').cleared, true)
assert.strictEqual(f.departmentRowChange(1, 'remove').count, 1)
assert.strictEqual(f.departmentRowChange(4, 'remove').count, 3)
assert.strictEqual(f.departmentRowChange(4, 'remove').cleared, undefined)

const four = f.departmentLayout(['製造１課', '製造２課', '製造３課', '製造４課', '衛生'])
assert.strictEqual(four.cols, 4)
assert.deepStrictEqual(four.rows.map((row) => row.length), [4, 1])
assert.strictEqual(f.departmentLayout(['インフォメーション']).cols, 3)
assert.strictEqual(f.departmentLayout(['あいうえおかきくけこさし']).cols, 2)

assert.strictEqual(f.savedFixedLabelSelectValue(12), 'saved:12')
assert.strictEqual(f.parseSavedFixedLabelSelectValue('saved:12'), 12)
assert.strictEqual(f.parseSavedFixedLabelSelectValue('daruma-zenibako'), null)
assert.strictEqual(f.parseSavedFixedLabelSelectValue('saved:0'), null)
assert.strictEqual(f.parseSavedFixedLabelSelectValue('saved:01'), 1)
assert.strictEqual(f.parseSavedFixedLabelSelectValue('saved:1abc'), null)

const cell = f.tierCellHtml(['石屋商事', '長尾様行', '納品日　月　日'])
assert.strictEqual(count(cell, /class="tierline"/g), 3)
assert.throws(() => f.departmentCellHtml('', []), /部署名|顧客名/)

console.log('ok')
