const assert = require('assert');
const fs = require('fs');
const vm = require('vm');
const source = fs.readFileSync(require.resolve('../sheet.js'), 'utf8');
const permissions = source.match(/function canWriteFixedLabelFormats\(\).*\nfunction canViewFixedLabelFormats\(\).*/)[0];
const context = vm.createContext({});
vm.runInContext(permissions, context);
// sheet.js loads before account.js defines currentUser. Initialization must continue.
assert.strictEqual(vm.runInContext('canWriteFixedLabelFormats()', context), false);
assert.strictEqual(vm.runInContext('canViewFixedLabelFormats()', context), false);
vm.runInContext('let currentUser = null', context);
assert.strictEqual(vm.runInContext('canWriteFixedLabelFormats()', context), false);
for (const role of ['owner', 'floor', 'floor2', 'floor3', 'warehouse', 'sales']) {
  context.role = role;
  vm.runInContext('currentUser = {role}', context);
  assert.strictEqual(vm.runInContext('canWriteFixedLabelFormats()', context), ['owner', 'floor', 'floor2', 'floor3'].includes(role));
  assert.strictEqual(vm.runInContext('canViewFixedLabelFormats()', context), role !== 'sales');
}
console.log('ok: fixed label permissions before and after account initialization');
