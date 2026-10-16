'use strict'
const assert = require('assert')
const fs = require('fs')
const path = require('path')
const vm = require('vm')

const src = fs.readFileSync(path.join(__dirname, '../static/app.js'), 'utf8')
const start = src.indexOf('const SIZE_SCHEMES=')
const end = src.indexOf('const PRODUCT_SUPPLIER_KEY=')
assert.ok(start >= 0 && end > start, 'SIZE_SCHEMES block missing')
const chunk = src.slice(start, end)
const sandbox = {}
vm.runInNewContext(
  chunk +
    '; this.SIZE_SCHEMES=SIZE_SCHEMES;' +
    '; this.detectSizeScheme=detectSizeScheme;' +
    '; this.shownSizeChips=shownSizeChips;' +
    '; this.sizeLabelAlreadySelected=sizeLabelAlreadySelected;' +
    '; this.compareDigitSizeLabels=compareDigitSizeLabels;' +
    '; this.sizeSchemeHint=sizeSchemeHint;' +
    '; this.isWaistPresetSize=isWaistPresetSize;',
  sandbox
)

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

test('waist presets are numeric ascending', () => {
  const sizes = Array.from(sandbox.SIZE_SCHEMES.waist.sizes).map(String)
  assert.strictEqual(
    JSON.stringify(sizes),
    JSON.stringify(['70', '73', '76', '79', '82', '85', '88', '91', '95', '100', '105', '110', '115', '120', '125'])
  )
  const nums = sizes.map(Number)
  assert.strictEqual(JSON.stringify(nums), JSON.stringify([...nums].sort((a, b) => a - b)))
})

test('detectSizeScheme picks waist when waist presets dominate', () => {
  assert.strictEqual(sandbox.detectSizeScheme(['70', '82', '100']), 'waist')
  assert.strictEqual(sandbox.detectSizeScheme(['5号', '7号', '9号']), 'gou')
  assert.strictEqual(sandbox.detectSizeScheme(['S', 'M', 'L']), 'letter')
  // W70 alone is not a waist preset — stay on letter default
  assert.strictEqual(sandbox.detectSizeScheme(['W70']), 'letter')
  assert.strictEqual(sandbox.detectSizeScheme(['W70', '70', '76']), 'waist')
})

test('shownSizeChips sorts digit sizes numerically and keeps W70 distinct', () => {
  const shown = Array.from(sandbox.shownSizeChips('waist', ['100', 'W70', '72', '70'])).map(String)
  assert.strictEqual(JSON.stringify(shown.slice(0, 4)), JSON.stringify(['70', '72', '73', '76']))
  assert.ok(shown.includes('100'))
  assert.ok(shown.includes('W70'))
  assert.ok(shown.indexOf('70') < shown.indexOf('72'))
  assert.ok(shown.indexOf('72') < shown.indexOf('73'))
  assert.ok(shown.indexOf('100') < shown.indexOf('W70'))
  // Switching scheme view does not drop selected non-preset labels from the chip list
  const letterView = Array.from(sandbox.shownSizeChips('letter', ['70', 'W70', 'M'])).map(String)
  assert.ok(letterView.includes('70'))
  assert.ok(letterView.includes('W70'))
  assert.ok(letterView.includes('M'))
})

test('exact-label dedupe: 70 vs W70 are separate', () => {
  assert.strictEqual(sandbox.sizeLabelAlreadySelected(['70'], '70'), true)
  assert.strictEqual(sandbox.sizeLabelAlreadySelected(['70'], 'W70'), false)
  assert.strictEqual(sandbox.sizeLabelAlreadySelected(['W70'], '70'), false)
})

test('waist hint mentions keep-on-switch', () => {
  assert.ok(sandbox.sizeSchemeHint('waist').includes('すでに選んだサイズはそのまま残ります'))
})

if (process.exitCode) process.exit(process.exitCode)
console.log('all waist size scheme unit tests passed')
