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
