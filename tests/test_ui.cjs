const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('app/ui/app.js', 'utf8');
const helper = source.slice(0, source.indexOf('\n/**'));
const context = vm.createContext({});
vm.runInContext(helper, context);
test('file metadata escapes HTML and quoted attributes', () => {
  assert.equal(context.escapeHtml('<img src=x onerror="alert(1)">'),
    '&lt;img src=x onerror=&quot;alert(1)&quot;&gt;');
  assert.equal(context.escapeHtml("a&b's.tmp"), 'a&amp;b&#39;s.tmp');
  assert.equal(context.escapeHtml(null), '');
});

test('CSV cells are quoted and cannot run as spreadsheet formulas', () => {
  assert.equal(context.csvCell('a"b'), '"a""b"');
  assert.equal(context.csvCell('=HYPERLINK("x")'), '"\'=HYPERLINK(""x"")"');
  assert.equal(context.csvCell('-rf.tmp'), '"\'-rf.tmp"');
  const csv = context.buildCsv([{ name: 'a.tmp', is_directory: false, category: 'Temporary & Cache Files',
    size_bytes: 5, size_formatted: '5 B', modified_date: 'd', risk_level: 'Safe', path: 'C:\\a.tmp', reason: 'r' }]);
  const lines = csv.slice(1).split('\r\n');
  assert.equal(lines.length, 2);
  assert.ok(lines[1].startsWith('"a.tmp","File","Temporary & Cache Files",5,'));
});

test('space breakdown keeps a fixed color slot per category and drops empty ones', () => {
  const parts = context.spaceBreakdown({
    'Stale Large Files': 300, 'Old Java & Build Artifacts': 100, 'Empty Folders': 0,
  });
  // Compare as JSON: arrays built inside the vm context belong to another realm
  assert.equal(JSON.stringify(parts.map((p) => [p.label, p.slot, p.percent])),
    JSON.stringify([['Builds', 2, 25], ['Stale large', 6, 75]]));
  assert.equal(context.spaceBreakdown({}).length, 0);
});
