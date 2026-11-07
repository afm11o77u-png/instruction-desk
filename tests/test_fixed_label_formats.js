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

const plain = {
  name: '固定だけ',
  kind: 'departments',
  payload: { customer: 'サザエ食品', departments: ['事務所', '工場'] },
}
const plainBefore = JSON.stringify(plain)
const plainHtml = f.savedFixedLabelPreviewHtml(plain, 1, null, '宮の沢店')
assert.strictEqual(JSON.stringify(plain), plainBefore)
assert.ok(plainHtml.includes('>サザエ食品</div>') || plainHtml.includes('サザエ食品'))
assert.ok(!plainHtml.includes('宮の沢店'))
assert.ok(!plainHtml.includes('class="title-store"'))
assert.ok(plainHtml.includes('class="title"'))

const slotted = {
  name: '店舗ひな形',
  kind: 'departments',
  payload: { customer: 'サザエ食品', departments: ['事務所', '工場'], storeSlot: true },
}
const slottedBefore = JSON.stringify(slotted)
assert.strictEqual(f.validateSavedFixedLabelPrintInput(slotted, 1, '').ok, false)
assert.strictEqual(f.validateSavedFixedLabelPrintInput(slotted, 1, '   ').ok, false)
const printed = f.savedFixedLabelPreviewHtml(slotted, 1, null, '宮の沢店')
assert.strictEqual(JSON.stringify(slotted), slottedBefore)
const storeCell = printed.slice(printed.indexOf('<section class="cell depts">'))
assert.ok(storeCell.includes('class="title title-row"'))
assert.ok(storeCell.includes('class="title-customer"'))
assert.ok(storeCell.includes('>サザエ食品</div>'))
assert.ok(storeCell.includes('class="title-store"'))
assert.ok(storeCell.includes('>宮の沢店</div>'))
assert.ok(storeCell.indexOf('title-customer') < storeCell.indexOf('title-store'))
assert.ok(storeCell.indexOf('title-store') < storeCell.indexOf('class="checks'))
assert.ok(!storeCell.includes('title-stack'))
assert.ok(f.FIXED_LABEL_CSS.includes('flex-direction:row'))
assert.ok(f.FIXED_LABEL_CSS.includes('gap:1em'))
assert.ok(f.FIXED_LABEL_CSS.includes('overflow-wrap:anywhere'))
assert.ok(!plainHtml.includes('class="title title-row"'))
assert.ok(printed.includes('新規　交換　その他'))
assert.ok(printed.includes('>NO.</span>'))
assert.strictEqual(count(printed, /宮の沢店/g), 4)
assert.ok(!printed.includes('storeSlot'))

const evilStore = f.savedFixedLabelPreviewHtml(slotted, 1, null, '<script>alert(1)</script>')
assert.ok(!evilStore.includes('<script>'))
assert.ok(evilStore.includes('&lt;script&gt;alert(1)&lt;/script&gt;'))

const preview = f.validateFixedLabelPreviewInput({
  name: '',
  kind: 'tiers',
  sheetCount: 2,
  payload: { lines: ['上', '中', '下'] },
})
assert.strictEqual(preview.ok, true)
assert.strictEqual(preview.name, '入力中の書式')
assert.strictEqual(preview.sheetCount, 2)
assert.strictEqual(f.validateFixedLabelPreviewInput({
  name: '',
  kind: 'departments',
  sheetCount: 1,
  storeName: '銭函店',
  payload: { customer: 'だるま食品', departments: ['製造'], storeSlot: true, storeName: '保存してはいけない' },
}).storeName, '銭函店')
assert.strictEqual(f.validateFixedLabelPreviewInput({
  kind: 'departments',
  sheetCount: 1,
  payload: { customer: 'だるま食品', departments: ['製造'], storeSlot: true },
}).ok, false)
assert.strictEqual(f.validateFixedLabelPreviewInput({
  kind: 'tiers',
  sheetCount: 1,
  payload: { lines: ['上', '', '下'] },
}).ok, false)
assert.strictEqual(f.validateFixedLabelDraft({
  name: '店舗ひな形',
  kind: 'departments',
  payload: { customer: 'サザエ食品', departments: ['事務所'], storeSlot: true, storeName: '宮の沢店' },
}).payload.storeName, undefined)
assert.strictEqual(f.validateFixedLabelDraft({
  name: '店舗ひな形',
  kind: 'departments',
  payload: { customer: 'サザエ食品', departments: ['事務所'], storeSlot: true },
}).payload.storeSlot, true)
assert.strictEqual(f.validateFixedLabelDraft({
  name: '普通',
  kind: 'departments',
  payload: { customer: 'サザエ食品', departments: ['事務所'] },
}).payload.storeSlot, undefined)

function deptCell(payload, storeName) {
  return f.savedFixedLabelPreviewHtml({ name: '確認', kind: 'departments', payload }, 1, null, storeName)
    .slice(f.savedFixedLabelPreviewHtml({ name: '確認', kind: 'departments', payload }, 1, null, storeName).indexOf('<section'))
}
const withNo = deptCell({ customer: 'サザエ食品', departments: ['事務所'] })
assert.ok(withNo.includes('class="no"'))
assert.ok(withNo.includes('>NO.</span>'))
assert.ok(withNo.includes('>様</div>'))
assert.ok(withNo.includes('新規　交換　その他'))
assert.ok(withNo.indexOf('class="checks') < withNo.indexOf('class="no"'))
assert.ok(withNo.indexOf('class="no"') < withNo.indexOf('class="name-row"'))

const withoutNo = deptCell({ customer: 'サザエ食品', departments: ['事務所'], printNo: false })
assert.ok(!withoutNo.includes('class="no"'))
assert.ok(!withoutNo.includes('>NO.</span>'))
assert.ok(withoutNo.includes('class="idblock"'))
assert.ok(withoutNo.includes('class="name-row"'))
assert.ok(withoutNo.includes('>様</div>'))
assert.ok(withoutNo.includes('新規　交換　その他'))
assert.ok(withoutNo.includes('>□</span>事務所'))
assert.ok(withoutNo.indexOf('class="checks') < withoutNo.indexOf('class="name-row"'))
assert.ok(withoutNo.indexOf('class="name-row"') < withoutNo.indexOf('class="footer"'))

const withoutNoStore = deptCell({
  customer: 'サザエ食品', departments: ['事務所'], printNo: false, storeSlot: true,
}, '札幌店')
assert.ok(withoutNoStore.includes('class="title title-row"'))
assert.ok(withoutNoStore.includes('>札幌店</div>'))
assert.ok(withoutNoStore.includes('>サザエ食品</div>'))
assert.ok(withoutNoStore.indexOf('title-customer') < withoutNoStore.indexOf('title-store'))
assert.ok(!withoutNoStore.includes('class="no"'))
assert.ok(withoutNoStore.includes('>様</div>'))
const longName = 'あ'.repeat(40)
const longHtml = deptCell({
  customer: longName, departments: ['事務所'], printNo: true, storeSlot: true,
}, longName)
assert.ok(longHtml.includes(longName))
assert.ok(longHtml.includes('class="title title-row"'))
assert.ok(longHtml.includes('class="no"'))
assert.ok(f.FIXED_LABEL_CSS.includes('.cell{') && f.FIXED_LABEL_CSS.includes('overflow:hidden'))
const noStoreNoLine = deptCell({ customer: 'サザエ食品', departments: ['事務所'], printNo: false })
assert.ok(!noStoreNoLine.includes('class="title title-row"'))
assert.ok(!noStoreNoLine.includes('class="no"'))
assert.ok(noStoreNoLine.includes('class="title"'))

const explicitOn = deptCell({ customer: 'サザエ食品', departments: ['事務所'], printNo: true })
assert.ok(explicitOn.includes('class="no"'))

const savedOff = f.validateFixedLabelDraft({
  name: 'Noなし',
  kind: 'departments',
  payload: { customer: 'サザエ食品', departments: ['事務所'], printNo: false },
})
assert.strictEqual(savedOff.payload.printNo, false)
const savedOn = f.validateFixedLabelDraft({
  name: 'Noあり',
  kind: 'departments',
  payload: { customer: 'サザエ食品', departments: ['事務所'], printNo: true },
})
assert.strictEqual(savedOn.payload.printNo, undefined)
const legacy = f.validateFixedLabelDraft({
  name: '旧',
  kind: 'departments',
  payload: { customer: 'サザエ食品', departments: ['事務所'] },
})
assert.strictEqual(legacy.payload.printNo, undefined)
assert.ok(deptCell(legacy.payload).includes('class="no"'))

const tierNo = f.savedFixedLabelPreviewHtml({
  name: '三段',
  kind: 'tiers',
  payload: { lines: ['上', '中', '下'], printNo: false },
}, 1)
assert.ok(!tierNo.includes('class="no"'))
assert.ok(!tierNo.includes('>NO.</span>'))
assert.strictEqual(f.validateFixedLabelDraft({
  name: '三段保存',
  kind: 'tiers',
  payload: { lines: ['上', '中', '下'], printNo: false },
}).payload.printNo, undefined)

const daruma = f.fixedLabelPreviewHtml('daruma-zenibako', 1)
assert.ok(daruma.includes('class="no"'))
assert.ok(daruma.includes('>NO.</span>'))
const ishiyaPage = f.fixedLabelPreviewHtml('ishiya-2242', 1)
assert.ok(!ishiyaPage.includes('class="no"'))
assert.ok(!ishiyaPage.includes('class="cell tiers"'))

assert.strictEqual(f.validateFixedLabelDraft({
  name: '不正',
  kind: 'departments',
  payload: { customer: 'サザエ食品', departments: ['事務所'], printNo: 'no' },
}).ok, false)

console.log('ok')
