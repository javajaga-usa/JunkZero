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

test('macOS wording swaps Windows names and example paths, Windows text is untouched', () => {
  assert.equal(context.platformText('Show in Windows Explorer', 'macos'), 'Show in Finder');
  assert.equal(context.platformText('Move Selected to Recycle Bin', 'macos'), 'Move Selected to Trash');
  assert.equal(context.platformText('Runs through Windows Task Scheduler.', 'macos'), 'Runs through macOS launchd.');
  assert.equal(context.platformText('e.g. C:\\ or C:\\Users\\YourName\\Downloads', 'macos'), 'e.g. / or /Users/yourname/Downloads');
  assert.equal(context.platformText('Move Selected to Recycle Bin', 'windows'), 'Move Selected to Recycle Bin');
  assert.equal(context.platformText('Show in Windows Explorer', 'linux'), 'Show in the file manager');
  assert.equal(context.platformText('Move Selected to Recycle Bin', 'linux'), 'Move Selected to Trash');
  assert.equal(context.platformText('e.g. C:\\ or C:\\Users\\YourName\\Downloads', 'linux'),
    'e.g. /home/yourname or /home/yourname/Downloads');
  assert.equal(context.platformText('Show in Windows Explorer', 'freebsd'), 'Show in Windows Explorer');
});

test('delete dialog lists personal, cloud and no-Recycle-Bin items before the details', () => {
  const lines = context.deletePreviewLines({
    count: 3, bytes_formatted: '3 KB', personal_count: 1, personal_examples: ['tax.pdf'],
    cloud: { OneDrive: 2 }, no_recycle_count: 1, truncated: false, needs_typed_confirm: true,
    top_folders: [{ folder: 'C:\\Users\\a\\Downloads', count: 3, bytes_formatted: '3 KB' }],
    types: [{ type: '.pdf', count: 1 }, { type: '.tmp', count: 2 }],
  }, 'Trash');
  assert.equal(JSON.stringify(lines.map((l) => l.level)),
    JSON.stringify(['warning', 'warning', 'info', 'info', 'info']));
  assert.match(lines[0].text, /1 personal file .*tax\.pdf/);
  assert.match(lines[1].text, /2 items in OneDrive/);
  assert.match(lines[2].text, /without a Trash/);
  assert.equal(lines[4].text, 'Types: .pdf 1, .tmp 2');
});

test('a large delete with nothing personal still explains why DELETE must be typed', () => {
  const lines = context.deletePreviewLines({
    count: 1200, bytes_formatted: '7 GB', personal_count: 0, cloud: {}, no_recycle_count: 0,
    truncated: false, needs_typed_confirm: true, top_folders: [], types: [],
  });
  assert.equal(lines[0].level, 'warning');
  assert.match(lines[0].text, /unusually large delete \(1,200 items, 7 GB\)/);
});

test('file type input accepts common spellings and drops repeats', () => {
  const { types, invalid } = context.parseFileTypes('MP4, .mov *.mkv  tar.gz;mp4');
  assert.equal(JSON.stringify(types), JSON.stringify(['mp4', 'mov', 'mkv', 'tar.gz']));
  assert.equal(invalid.length, 0);
});

test('file type input refuses paths and match-everything wildcards', () => {
  const { types, invalid } = context.parseFileTypes('* *.* ../x C:\\Videos mp4/ ok');
  assert.equal(JSON.stringify(types), JSON.stringify(['ok']));
  assert.equal(JSON.stringify(invalid), JSON.stringify(['*', '*.*', '../x', 'C:\\Videos', 'mp4/']));
});

test('preset chips show whether a group is fully, partly or not chosen', () => {
  assert.equal(context.presetState(['mp4', 'mov'], ['mp4', 'mov', 'log']), 'true');
  assert.equal(context.presetState(['mp4', 'mov'], ['mp4']), 'mixed');
  assert.equal(context.presetState(['mp4', 'mov'], []), 'false');
});

test('chosen file types take the next color slot after program leftovers', () => {
  const parts = context.spaceBreakdown({ 'Chosen File Types': 10, 'Program Leftovers': 10 });
  assert.equal(JSON.stringify(parts.map((p) => [p.label, p.slot])),
    JSON.stringify([['Leftovers', 9], ['File types', 10]]));
});

test('drive buttons match the scan location only when it is the whole drive', () => {
  assert.equal(context.samePath('C:\\', 'c:'), true);
  assert.equal(context.samePath('/', '/'), true);
  assert.equal(context.samePath('/Volumes/Backup', '/Volumes/Backup/'), true);
  assert.equal(context.samePath('/', '/Users/jaga'), false);
  assert.equal(context.samePath('C:\\', 'C:\\Users'), false);
  assert.equal(context.samePath('', ''), false);
});

test('exclusion rules match like the server: paths cover what is inside, patterns match names or paths', () => {
  const rules = ['C:\\Users\\me\\Keep', '*.iso', '/Volumes/Red/a[0-9].txt', 'report?.pdf'];
  assert.equal(context.exclusionRuleFor('c:\\users\\me\\keep\\x.tmp', rules), 'C:\\Users\\me\\Keep');
  assert.equal(context.exclusionRuleFor('C:\\Users\\me\\Keep', rules), 'C:\\Users\\me\\Keep');
  assert.equal(context.exclusionRuleFor('C:\\Users\\me\\Keeper\\x', rules), null);
  assert.equal(context.exclusionRuleFor('/x/Win.ISO', rules), '*.iso');
  assert.equal(context.exclusionRuleFor('/Volumes/Red/a7.txt', rules), '/Volumes/Red/a[0-9].txt');
  assert.equal(context.exclusionRuleFor('/Volumes/Red/ab.txt', rules), null);
  assert.equal(context.exclusionRuleFor('/d/report1.pdf', rules), 'report?.pdf');
  assert.equal(context.exclusionRuleFor('/d/report12.pdf', rules), null);
  assert.equal(context.exclusionRuleFor('/d/a.(1).log', ['a.(1).log']), null);
  assert.equal(context.exclusionRuleFor('/d/a.(1).log', ['*(1).log']), '*(1).log');
  assert.equal(context.exclusionRuleFor('/d/x', []), null);
});

test('a deleted folder covers what is inside it, not a sibling that starts with the same name', () => {
  assert.equal(context.pathIsWithin('C:\\foo\\bar\\x.tmp', 'C:\\foo\\bar'), true);
  assert.equal(context.pathIsWithin('c:\\FOO\\bar', 'C:\\foo\\bar\\'), true);
  assert.equal(context.pathIsWithin('C:\\foo\\bar2\\x.tmp', 'C:\\foo\\bar'), false);
  assert.equal(context.pathIsWithin('C:\\foo\\bar2', 'C:\\foo\\bar'), false);
  assert.equal(context.pathIsWithin('/home/me/a/b', '/home/me/a'), true);
  assert.equal(context.pathIsWithin('/home/me/ab', '/home/me/a'), false);
  assert.equal(context.pathIsWithin('/home/me', ''), false);
});

test('scan steps have plain-language labels, unknown steps none', () => {
  assert.equal(context.scanPhaseLabel('walking'), 'Scanning folders');
  assert.equal(context.scanPhaseLabel('leftovers'), 'Checking program leftovers');
  assert.equal(context.scanPhaseLabel('empty_folders'), 'Looking for empty folders');
  assert.equal(context.scanPhaseLabel('duplicates'), 'Comparing duplicates');
  assert.equal(context.scanPhaseLabel(undefined), '');
  assert.equal(context.scanPhaseLabel('something-new'), '');
});

test('delete progress reads "Deleting 1,200 of 3,000" with a matching bar', () => {
  assert.equal(context.deleteProgressText({ running: true, done: 1200, total: 3000 }), 'Deleting 1,200 of 3,000');
  assert.equal(context.deleteProgressPercent({ running: true, done: 1200, total: 3000 }), 40);
  // Not started yet, finished, or nothing known: no made-up numbers
  assert.equal(context.deleteProgressText({ running: false, done: 0, total: 0 }), 'Deleting...');
  assert.equal(context.deleteProgressText(null), 'Deleting...');
  assert.equal(context.deleteProgressPercent({ running: true, done: 0, total: 0 }), null);
  assert.equal(context.deleteProgressText({ running: true, done: 5000, total: 3000 }), 'Deleting 3,000 of 3,000');
});

test('the error list copies as one path and reason per line', () => {
  assert.equal(context.errorListText([{ path: 'C:\\a.tmp', error: 'In use' }, { path: '/b', error: 'Denied' }]),
    'C:\\a.tmp\tIn use\n/b\tDenied');
  assert.equal(context.errorListText([]), '');
  assert.equal(context.errorListText(undefined), '');
});

test('update check wording: newer version with a link, up to date, or the error', () => {
  const newer = context.updateCheckMessage({ current: '1.2.0', latest: '1.3.0', newer: true, url: 'https://x/r', error: '' });
  assert.equal(newer.text, 'JunkZero 1.3.0 is available (you have 1.2.0).');
  assert.equal(newer.url, 'https://x/r');
  const same = context.updateCheckMessage({ current: '1.2.0', latest: '1.2.0', newer: false, url: 'https://x/r', error: '' });
  assert.equal(same.text, 'You have the latest version (1.2.0).');
  assert.equal(same.url, '');
  const failed = context.updateCheckMessage({ current: '1.2.0', latest: null, newer: false, url: '', error: 'offline' });
  assert.equal(failed.text, 'Could not check for updates: offline');
});

test('Tab wraps around inside a dialog and leaves the middle alone', () => {
  assert.equal(context.trappedFocusIndex(3, 2, false), 0);
  assert.equal(context.trappedFocusIndex(3, 0, true), 2);
  assert.equal(context.trappedFocusIndex(3, 1, false), -1);
  assert.equal(context.trappedFocusIndex(3, -1, false), 0);
  assert.equal(context.trappedFocusIndex(3, -1, true), 2);
  assert.equal(context.trappedFocusIndex(0, -1, false), -1);
});

test('delete dialog says what happens on each disk when it is not simply the Trash', () => {
  const lines = context.deletePreviewLines({
    count: 6, bytes_formatted: '6 KB', personal_count: 0, cloud: {}, no_recycle_count: 3, truncated: false,
    needs_typed_confirm: false, top_folders: [], types: [],
    disks: [
      { name: 'J Red', fs_label: 'exFAT', destination: 'holding', count: 3, bytes_formatted: '3 KB',
        reason: 'on exFAT disks macOS may erase items instead of keeping them in the Trash' },
      { name: 'Macintosh HD', fs_label: 'APFS', destination: 'trash', count: 2, bytes_formatted: '2 KB', reason: '' },
      { name: 'Windows', fs_label: 'NTFS', destination: 'read_only', count: 1, bytes_formatted: '1 KB',
        reason: 'macOS can only read NTFS disks, so nothing on it can be deleted' },
    ],
  }, 'Trash');
  const texts = lines.map((l) => l.text);
  assert.ok(texts.some((t) => /^J Red \(exFAT\): 3 items \(3 KB\) go to a hidden JunkZero folder on that disk for 7 days instead of the Trash, because on exFAT/.test(t)));
  assert.ok(texts.includes('Macintosh HD (APFS): 2 items (2 KB) go to the Trash'));
  assert.ok(lines.some((l) => l.level === 'warning' && /^Windows \(NTFS\): 1 item can't be deleted: macOS can only read NTFS/.test(l.text)));
  assert.ok(!texts.some((t) => /without a Trash/.test(t)));
});

test('a single ordinary disk adds no disk line', () => {
  const lines = context.deletePreviewLines({
    count: 2, bytes_formatted: '2 KB', personal_count: 0, cloud: {}, no_recycle_count: 0, truncated: false,
    needs_typed_confirm: false, top_folders: [], types: [],
    disks: [{ name: 'Macintosh HD', fs_label: 'APFS', destination: 'trash', count: 2, bytes_formatted: '2 KB', reason: '' }],
  }, 'Trash');
  assert.equal(lines.length, 0);
});

test('scan location note appears only for disks without a dependable Trash', () => {
  assert.equal(context.diskNoteText({ destination: 'trash', name: 'Macintosh HD' }, 'Trash'), '');
  assert.match(context.diskNoteText({ destination: 'holding', name: 'J Red', fs_label: 'exFAT', holding_days: 7,
    reason: 'on exFAT disks macOS may erase items instead of keeping them in the Trash' }, 'Trash'),
  /^J Red \(exFAT\): deleted items go to a hidden JunkZero folder on this disk for 7 days instead of the Trash/);
  assert.match(context.diskNoteText({ destination: 'read_only', name: 'Windows', fs_label: 'NTFS',
    reason: 'macOS can only read NTFS disks, so nothing on it can be deleted' }, 'Trash'), /can scan it, but can't delete anything/);
});

test('after a scan, unreadable folders are reported, with the privacy fix on a Mac', () => {
  assert.equal(context.unreadableText({ unreadable_dirs: 0 }), '');
  assert.equal(context.unreadableText({ unreadable_dirs: 2, unreadable_examples: ['/Users/x/Private'] }),
    "2 folders couldn't be read (no permission), such as /Users/x/Private, so they weren't scanned.");
  const mac = context.unreadableText({ unreadable_dirs: 1, privacy_blocked_dirs: 1, unreadable_examples: ['/Volumes/J Red'],
    privacy_fix: 'Give Terminal Full Disk Access in System Settings > Privacy & Security > Full Disk Access, then restart JunkZero' });
  assert.match(mac, /^macOS kept JunkZero out of 1 folder, such as \/Volumes\/J Red, so it wasn't scanned\. Give Terminal Full Disk Access/);
});

test('after a scan, junk the user may not remove is reported instead of listed', () => {
  assert.equal(context.notAllowedText({ not_allowed_items: 0 }), '');
  assert.equal(context.notAllowedText({ not_allowed_items: 2, not_allowed_bytes: 2048,
    not_allowed_examples: ["/media/u/disk/target (it belongs to the system, and JunkZero doesn't use administrator rights)"] }),
    "2 items (2.00 KB) looked like junk but aren't listed, because you don't have permission to remove them. "
    + "One of them: /media/u/disk/target (it belongs to the system, and JunkZero doesn't use administrator rights).");
});

test('delete dialog warns about selected items further down the list', () => {
  assert.equal(context.unseenLine(6502, 7002),
    "6,502 of these 7,002 items are further down the list and haven't been shown. Cancel and use Show all to look through them first, or type DELETE to go ahead.");
});

test('treemap boxes fill the map exactly, biggest first, with the rest grouped', () => {
  const folders = [
    { name: 'Videos', path: '/h/Videos', size_bytes: 600 },
    { name: 'Music', path: '/h/Music', size_bytes: 200 },
    { name: 'Empty', path: '/h/Empty', size_bytes: 0 },
  ];
  // 100 bytes in folders the server didn't list, 100 bytes of loose files
  const tiles = context.treemapTiles(folders, 100, 1000);
  assert.equal(JSON.stringify(tiles.map((t) => [t.name, t.kind, Math.round(t.percent)])),
    JSON.stringify([['Videos', 'folder', 60], ['Music', 'folder', 20], ['Smaller folders', 'other', 10],
      ['Files directly here', 'files', 10]]));
  const area = tiles.reduce((acc, t) => acc + t.w * t.h, 0);
  assert.ok(Math.abs(area - 10000) < 1e-6);
  tiles.forEach((t) => {
    assert.ok(t.x >= -1e-9 && t.y >= -1e-9 && t.x + t.w <= 100 + 1e-9 && t.y + t.h <= 100 + 1e-9);
    assert.ok(Math.abs((t.w * t.h) / 100 - t.percent) < 1e-6);
  });
  assert.equal(context.treemapTiles([], 0, 0).length, 0);
  // Past the tile limit, the smaller folders are grouped too
  const many = Array.from({ length: 5 }, (_, i) => ({ name: `f${i}`, path: `/f${i}`, size_bytes: 10 }));
  assert.equal(context.treemapTiles(many, 0, 50, 3).map((t) => t.name).sort().join(','), 'Smaller folders,f0,f1,f2');
});

test('free-space chart: line, area and cleanup markers inside the box', () => {
  const points = [{ at: 1000, free: 50 }, { at: 3000, free: 80 }, { at: 2000, free: 40 }];
  const model = context.storageChartModel(points, [{ at: 2500, freed_bytes: 30 }, { at: 9999, freed_bytes: 1 }], 200, 100);
  assert.equal(model.count, 3);
  assert.equal(model.first.free, 50);
  assert.equal(model.last.free, 80);
  assert.equal(model.change, 30);
  assert.ok(model.line.startsWith('M6.0,'));
  assert.ok(model.area.endsWith('Z'));
  assert.equal(model.markers.length, 1);
  assert.equal(model.markers[0].x, 6 + (1500 / 2000) * 188);
  assert.ok(model.lo <= 40 && model.hi >= 80);
  const one = context.storageChartModel([{ at: 5, free: 10 }], [], 200, 100);
  assert.equal(one.count, 1);
  assert.ok(one.line.startsWith('M6.0,') && one.line.includes('L194.0,'));
  assert.equal(context.storageChartModel([], [], 200, 100), null);
});

test('space-still-held text says only JunkZero items are emptied', () => {
  const none = context.reclaimText({ junkzero_count: 0, recycle_bin_bytes: 2048, older_than_days: 7 }, 'Trash');
  assert.equal(none.title, 'Nothing JunkZero deleted is still taking space');
  assert.match(none.text, /^The Trash holds 2\.00 KB in all\. /);
  const some = context.reclaimText({ junkzero_count: 3, junkzero_bytes: 3 * 1048576, holding_bytes: 1048576,
    recycle_bin_bytes: null, old_count: 2, old_bytes: 2 * 1048576, older_than_days: 7 });
  assert.equal(some.title, '3.00 MB you deleted with JunkZero still takes space');
  assert.equal(some.text, "3 items from your cleanups are still in the Recycle Bin (1.00 MB of it in JunkZero's holding folders), "
    + "so that space isn't free yet. 2.00 MB of it is from cleanups at least 7 days old. Everything else in the Recycle Bin is left alone.");
  assert.match(context.reclaimText({ junkzero_count: 1, junkzero_bytes: 5, old_count: 0, older_than_days: 1 }).text,
    /None of it is from cleanups at least 1 day old/);
});

test('new categories get their own color slots', () => {
  const parts = context.spaceBreakdown({ 'Duplicate Folders': 10, 'Old Project Builds': 30 });
  assert.equal(JSON.stringify(parts.map((p) => [p.label, p.slot])),
    JSON.stringify([['Duplicate folders', 11], ['Old projects', 12]]));
});
