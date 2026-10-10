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

test('old downloads take the next color slot without moving existing ones', () => {
  const parts = context.spaceBreakdown({ 'Old Downloads': 50, 'Installers, Setup Archives & OS Images': 50 });
  assert.equal(JSON.stringify(parts.map((p) => [p.label, p.slot])),
    JSON.stringify([['Installers', 1], ['Old downloads', 7]]));
});

test('my junk rules take their own color slot after the existing ones', () => {
  const parts = context.spaceBreakdown({ 'My Junk Rules': 10, 'Old Downloads': 10 });
  assert.equal(JSON.stringify(parts.map((p) => [p.label, p.slot])),
    JSON.stringify([['Old downloads', 7], ['My rules', 8]]));
});

test('parent folder handles Windows paths, drive roots and POSIX paths', () => {
  assert.equal(context.parentFolder('C:\\Users\\me\\Temp\\a.tmp'), 'C:\\Users\\me\\Temp');
  assert.equal(context.parentFolder('C:\\Users\\me\\node_modules\\'), 'C:\\Users\\me');
  assert.equal(context.parentFolder('C:\\a.tmp'), 'C:\\');
  assert.equal(context.parentFolder('/home/me/a.tmp'), '/home/me');
  assert.equal(context.parentFolder('/a.tmp'), '/');
  assert.equal(context.parentFolder('a.tmp'), '');
});

test('junk by folder groups case-insensitively and ranks by size', () => {
  const groups = context.junkByFolder([
    { path: 'C:\\Temp\\a.tmp', size_bytes: 10 },
    { path: 'c:\\temp\\b.tmp', size_bytes: 5 },
    { path: 'C:\\Downloads\\big.iso', size_bytes: 100 },
    { path: 'C:\\Empty\\Gone', size_bytes: 0 },
  ]);
  assert.equal(JSON.stringify(groups.map((g) => [g.folder, g.bytes, g.count])),
    JSON.stringify([['C:\\Downloads', 100, 1], ['C:\\Temp', 15, 2], ['C:\\Empty', 0, 1]]));
  assert.equal(context.junkByFolder([{ path: 'C:\\x\\a', size_bytes: 1 }, { path: 'C:\\y\\b', size_bytes: 2 }], 1).length, 1);
});

test('smart score bands match the backend thresholds', () => {
  assert.equal(context.scoreBand(75), 'delete');
  assert.equal(context.scoreBand(74), 'review');
  assert.equal(context.scoreBand(45), 'review');
  assert.equal(context.scoreBand(44), 'keep');
});

test('smart summary describes each band in plain words', () => {
  const fmt = (b) => `${b} B`;
  const summary = context.smartSummary([
    { name: 'a.tmp', category: 'Temporary & Cache Files', size_bytes: 30, score: 90 },
    { name: 'b.tmp', category: 'Temporary & Cache Files', size_bytes: 20, score: 80 },
    { name: 'gone', category: 'Empty Folders', size_bytes: 0, score: 75 },
    { name: 'Win11.iso', category: 'Installers, Setup Archives & OS Images', size_bytes: 500, score: 60, reason: 'OS / Disc image file (.ISO)' },
    { name: 'tool.exe', category: 'Installers, Setup Archives & OS Images', size_bytes: 9, score: 20,
      score_reasons: ['-25: you kept this through 2 earlier scans'] },
    { name: 'old-report.tmp', category: 'Temporary & Cache Files', size_bytes: 1 },
  ], fmt);
  assert.equal(JSON.stringify(summary.lines.map((l) => [l.band, l.text])), JSON.stringify([
    ['delete', '3 items (50 B) can go now with little risk, mostly temp & cache.'],
    ['review', '1 item (500 B) is worth a look first. The biggest is Win11.iso (500 B): OS / Disc image file (.ISO).'],
    ['keep', '1 item (9 B) looks worth keeping, including 1 item you kept after earlier scans.'],
  ]));
  assert.equal(summary.deleteCount, 3);
  assert.equal(context.smartSummary([], fmt).lines.length, 0);
  assert.equal(context.smartSummary([{ name: 'x', size_bytes: 1, score: 50, reason: 'r' }], fmt).lines[0].text,
    'Nothing here is a sure bet, so look before you delete.');
});

test('CSV export includes the smart score and its reasons', () => {
  const csv = context.buildCsv([{ name: 'a.tmp', is_directory: false, category: 'c', size_bytes: 5, size_formatted: '5 B',
    modified_date: 'd', risk_level: 'Safe', path: 'p', reason: 'r', score: 88, recommendation: 'Delete',
    score_reasons: ['Starts at 80: rated Safe', '+8: old'] }]);
  const [header, row] = csv.slice(1).split('\r\n');
  assert.ok(header.endsWith('"Smart Score","Recommendation","Why"'));
  assert.ok(row.endsWith(',88,"Delete","Starts at 80: rated Safe; +8: old"'));
});

test('category badges use the chart label and color slot, with a neutral fallback', () => {
  assert.equal(JSON.stringify(context.categoryBadge('Temporary & Cache Files')), JSON.stringify({ label: 'Temp & cache', slot: 3 }));
  assert.equal(JSON.stringify(context.categoryBadge('My Junk Rules')), JSON.stringify({ label: 'My rules', slot: 8 }));
  assert.equal(JSON.stringify(context.categoryBadge('Empty Folders')), JSON.stringify({ label: 'Empty folder', slot: 0 }));
});
