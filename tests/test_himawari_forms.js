'use strict'
/* ひまわり1〜4 定型書式（A4横4分割・同じカード4枠・空の□） */
const assert = require('assert')
const path = require('path')
const f = require(path.join(__dirname, '../fixed_label.js'))

const expected = {
  'himawari-1': ['ひまわり1', 5, ['タワー店', 'ファイターズ通り店', '豊平店', '月寒店', '清田店', '新琴似店', '篠路店', '真駒内店', 'タウン店']],
  'himawari-2': ['ひまわり2', 4, ['五所川原店', '八戸店', '能代店', '大曲店', '岩沼店', '北上店', '多賀城店']],
  'himawari-3': ['ひまわり3', 5, ['弟子屈店', '知床店', '美幌店', '北見店', '紋別店', '中標津店', '根室店', '釧路店', '帯広店', '音更店']],
  'himawari-4': ['ひまわり4', 4, ['苫小牧店', '函館店', '日乃出店', '伊達店', '滝川店', '留萌店', '士別店', '室蘭店']],
}
const listed = f.listFixedLabelFormats().map((x) => x.name)
for (const [id, [name, left, stores]] of Object.entries(expected)) {
  const fmt = f.getFixedLabelFormat(id)
  assert.ok(fmt, id)
  assert.strictEqual(fmt.name, name)
  assert.strictEqual(fmt.kind, 'cells')
  assert.ok(listed.includes(name))
  const full = stores.map((s) => 'ひまわり' + s)
  assert.deepStrictEqual(fmt.stores, full)
  const html = f.fixedLabelPagesHtml(id, 2)
  assert.strictEqual((html.match(/class="page"/g) || []).length, 2)
  assert.strictEqual((html.match(/class="cell hm"/g) || []).length, 8)
  const cell = fmt.buildCellHtml()
  const cols = cell.match(/<div class="col">(.*?)<\/div><\/div>|<div class="col">.*?(?=<div class="col">)/g)
  const order = [...cell.matchAll(/<span class="box"><\/span>([^<]+)<\/div>/g)].map((m) => m[1])
  assert.deepStrictEqual(order, full, id + ' order')
  const [l, r] = cell.split('<div class="col">').slice(1)
  assert.strictEqual((l.match(/class="store"/g) || []).length, left)
  assert.strictEqual((r.match(/class="store"/g) || []).length, full.length - left)
  assert.ok(!/box on|■|☑/.test(cell), 'checkboxes print empty')
  for (const part of ['様', '【備考】', 'memobox', 'エンパイアー', 'class="rule"', 'class="frame"']) assert.ok(cell.includes(part), part)
}
assert.ok(f.FIXED_LABEL_CSS.includes('.hm .frame'))
assert.ok(f.FIXED_LABEL_CSS.includes('border-left:1px dashed'))
assert.strictEqual(f.validateFixedLabelPrintInput({ formatId: 'himawari-1', sheetCount: 0 }).ok, false)
assert.ok(f.fixedLabelPreviewHtml('himawari-1', 1).includes('<html lang="ja">'))
for (const fam of ['"Yu Gothic"', 'Meiryo', '"MS Gothic"', '"Noto Sans CJK JP"']) assert.ok(f.FIXED_LABEL_CSS.includes(fam), fam)
assert.ok(!/\.hm[^{]*\{[^}]*(letter-spacing|scale|font-stretch)/.test(f.FIXED_LABEL_CSS), 'no condensing on himawari')
console.log('ok')
