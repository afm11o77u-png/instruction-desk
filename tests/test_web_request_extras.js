'use strict'
const assert = require('assert')
const path = require('path')
const X = require(path.join(__dirname, '..', 'web-request-extras.js'))

// 表示名：契約の表示名 → 商品のWEB表示名 → 在庫の商品名
assert.strictEqual(X.resolveName('', '', '在庫名'), '在庫名')
assert.strictEqual(X.resolveName('', ' WEB名 ', '在庫名'), 'WEB名')
assert.strictEqual(X.resolveName('契約名', 'WEB名', '在庫名'), '契約名')
assert.strictEqual(X.resolvePhoto('', 5, { photo_version: 2 }), '/api/web-request/product-photo/5?v=2')
assert.strictEqual(X.resolvePhoto('data:image/jpeg;base64,AA', 5, { photo_version: 2 }), 'data:image/jpeg;base64,AA')
assert.strictEqual(X.resolvePhoto('', 5, { photo_version: 0 }), '')

// 写真ファイルの事前チェック
assert.ok(X.checkPhotoFile({ type: 'image/gif', size: 10 }))
assert.ok(X.checkPhotoFile({ type: 'image/png', size: 3 * 1024 * 1024 + 1 }))
assert.strictEqual(X.checkPhotoFile({ type: 'image/webp', size: 1000 }), '')

// 手入力（＋ 制服を追加）
assert.strictEqual(X.validateManualItem({ name: '', qty: '1' }).ok, false)
assert.strictEqual(X.validateManualItem({ name: 'ポロシャツ', qty: '0' }).ok, false)
assert.strictEqual(X.validateManualItem({ name: 'ポロシャツ', qty: '1.5' }).ok, false)
assert.strictEqual(X.validateManualItem({ name: 'ポロシャツ', qty: '' }).ok, false)
assert.strictEqual(X.validateManualItem({ name: 'x'.repeat(101), qty: '1' }).ok, false)
const ok = X.validateManualItem({ name: ' ポロシャツ ', code: 'P-1', color: '紺', size: 'L', qty: '2', processing: 'ネーム', note: '急ぎ' })
assert.strictEqual(ok.ok, true)
assert.strictEqual(ok.item.manual, true)
assert.strictEqual(ok.item.name, 'ポロシャツ')
assert.strictEqual(ok.item.qty, 2)
assert.strictEqual(ok.item.processing_days, 0)
assert.strictEqual(ok.item.hem_processing, false)

// 顧客向けに「契約外」「手入力」を出さない。社内向けは「手入力」バッジと元の商品名
const cust = X.itemLineHtml(ok.item, { staff: false }) + X.cartLineHtml(ok.item, 0)
assert.ok(!cust.includes('契約外') && !cust.includes('手入力'))
assert.ok(cust.includes('品番 P-1') && cust.includes('加工の希望：ネーム'))
assert.ok(X.itemLineHtml(ok.item, { staff: true }).includes('手入力'))
const named = { name: 'WEB名', stock_name: '在庫名', color: '紺', size: 'M', qty: 1 }
assert.ok(X.itemLineHtml(named, { staff: true }).includes('在庫名（WEB表示名：WEB名）'))
assert.ok(!X.itemLineHtml(named, { staff: false }).includes('在庫名'))
assert.ok(X.itemLineHtml({ name: '<b>', qty: 1 }, {}).includes('&lt;b&gt;'))

// 会社の契約で使う商品
const data = {
  handles: [{ id: 11, customer_id: 1, product_id: 101 }, { id: 12, customer_id: 2, product_id: 102 }],
  profiles: [{ customer_id: 1, draft: { departments: [{ contracts: [{ uniforms: [{ customer_product_id: 11 }] }] }] } }, { customer_id: 2, draft: { departments: [{ contracts: [{ uniforms: [{ customer_product_id: 12 }] }] }] } }],
  scopeProfiles: [{ customer_id: 1, live: { departments: [{ contracts: [{ uniforms: [{ product_id: 103 }] }] }] } }],
}
assert.deepStrictEqual([...X.usedProductIds(data, 1)].sort(), [101, 103])
console.log('ok')

// 「＋ 制服を追加」の案内は最初から表示（フォーム内で重複させない）
{
  const src = require('fs').readFileSync(path.join(__dirname, '..', 'web-request-extras.js'), 'utf8')
  const hint = '一覧にない制服は、こちらに入力して追加できます。'
  assert.strictEqual(src.split(hint).length - 1, 1)
  assert.ok(src.indexOf(hint) < src.indexOf('id="manual-entry-open"'))
  assert.ok(src.indexOf(hint) < src.indexOf('id="manual-entry-form"'))
  assert.ok(!src.includes('契約外'))
  // 左の契約の制服一覧（#products）の下に置く
  assert.ok(src.includes("const items = $('#products');"))
}
