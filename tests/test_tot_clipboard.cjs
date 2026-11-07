const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const path = require('node:path');
let source = fs.readFileSync(path.join(__dirname, '../kintai-tot-attendance.js'), 'utf8');
source = source.replace("  if (document.readyState === 'loading')", '  globalThis.helpers = { checkedValues, clipboardData };\n  if (document.readyState === \'loading\')');
const context = { document: { readyState: 'loading', addEventListener() {} } };
vm.runInNewContext(source, context);
const { checkedValues, clipboardData } = context.helpers;
assert.ok(checkedValues({ start: '9:00', rest: '', end: '17:40' }).error);
assert.ok(checkedValues({ start: '9:00', rest: '1:10', end: '' }).error);
assert.ok(checkedValues({ start: '', rest: '0:00', end: '' }).error);
assert.ok(checkedValues({ start: '9:00', rest: '70', end: '17:40' }).error);
assert.equal(checkedValues({ start: '9:00', rest: '0:00', end: '15:00' }).error, undefined);
const rows = [
  { start: '9:00', rest: '1:10', end: '17:40' },
  { start: '', rest: '', end: '' },
  { start: '22:00', rest: '0:00', end: '1:30' },
  { start: '', rest: '', end: '' },
];
const result = clipboardData(rows);
assert.equal(result.text, '9:00\t1:10\t17:40\r\n\t\t\r\n22:00\t0:00\t25:30\r\n\t\t\r\n');
assert.equal((result.html.match(/<tr>/g) || []).length, 4);
assert.equal((result.html.match(/<td/g) || []).length, 12);
assert.ok(result.html.includes('x:num="' + (70 / 1440) + '"'));
assert.ok(result.html.includes('x:num="0"'));
console.log('Clipboard checks passed: three columns, blank rows, time serials, zero break, overnight.');
