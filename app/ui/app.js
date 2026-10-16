// Wording for the system the app runs on. The page is written for Windows; on macOS these
// phrases are swapped (longest first, so "Windows Explorer" wins over shorter matches).
const MAC_WORDING = [
  ['Include Windows junk locations (temp, crash dumps, browser caches)', 'Include junk locations (temp, app caches, logs)'],
  ['e.g. C:\\ or C:\\Users\\YourName\\Downloads', 'e.g. / or /Users/yourname/Downloads'],
  ['C:\\Users\\YourName\\Downloads', '/Users/yourname/Downloads'],
  ['C:\\Projects\\keep-this', '/Users/yourname/Projects/keep-this'],
  ['C:\\Projects\\scratch', '/Users/yourname/Projects/scratch'],
  ['Scan your temp folder, crash dumps and browser caches', 'Scan your temp folder, app caches, logs and Xcode build data'],
  ['App data folders in AppData', 'App data folders in Library/Application Support'],
  ['Windows Task Scheduler', 'macOS launchd'],
  ['Windows Explorer', 'Finder'],
  ['Recycle Bin', 'Trash'],
];

function platformText(text, platform) {
  let out = String(text ?? '');
  if (platform !== 'macos') return out;
  MAC_WORDING.forEach(([from, to]) => { out = out.split(from).join(to); });
  return out;
}

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, (char) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[char]));
}

// Spreadsheet apps run cells starting with = + - @ as formulas; prefix them so file names stay text
function csvCell(value) {
  let text = String(value ?? '');
  if (/^[=+\-@\t\r]/.test(text)) text = `'${text}`;
  return `"${text.replace(/"/g, '""')}"`;
}

function buildCsv(items) {
  const headers = ['Name', 'Type', 'Category', 'Size (Bytes)', 'Size Formatted', 'Modified Date', 'Risk Level', 'Path', 'Reason', 'Smart Score', 'Recommendation', 'Why'];
  const rows = items.map((i) => [
    csvCell(i.name),
    csvCell(i.is_directory ? 'Folder' : 'File'),
    csvCell(i.category),
    Number(i.size_bytes) || 0,
    csvCell(i.size_formatted),
    csvCell(i.modified_date),
    csvCell(i.risk_level),
    csvCell(i.path),
    csvCell(i.reason),
    Number(i.score) || 0,
    csvCell(i.recommendation),
    csvCell((i.score_reasons || []).join('; ')),
  ].join(','));
  return '\uFEFF' + [headers.map(csvCell).join(','), ...rows].join('\r\n');
}

// Reclaimable-space chart: categories in fixed color order (color follows the category, never its rank)
const SPACE_CATEGORIES = [
  { name: 'Installers, Setup Archives & OS Images', label: 'Installers' },
  { name: 'Old Java & Build Artifacts', label: 'Builds' },
  { name: 'Temporary & Cache Files', label: 'Temp & cache' },
  { name: 'Broken / Incomplete Downloads', label: 'Broken downloads' },
  { name: 'Duplicate Files', label: 'Duplicates' },
  { name: 'Stale Large Files', label: 'Stale large' },
  { name: 'Old Downloads', label: 'Old downloads' },
  { name: 'My Junk Rules', label: 'My rules' },
  { name: 'Program Leftovers', label: 'Leftovers' },
  { name: 'Chosen File Types', label: 'File types' },
];

function spaceBreakdown(categoryBytes) {
  const total = SPACE_CATEGORIES.reduce((acc, c) => acc + (categoryBytes[c.name] || 0), 0);
  return SPACE_CATEGORIES.map((c, idx) => ({
    ...c,
    slot: idx + 1,
    bytes: categoryBytes[c.name] || 0,
    percent: total > 0 ? ((categoryBytes[c.name] || 0) / total) * 100 : 0,
  })).filter((c) => c.bytes > 0);
}

// Short label and chart color slot for a category badge (slot 0 is neutral)
function categoryBadge(category) {
  const idx = SPACE_CATEGORIES.findIndex((c) => c.name === category);
  if (idx >= 0) return { label: SPACE_CATEGORIES[idx].label, slot: idx + 1 };
  return { label: category === 'Empty Folders' ? 'Empty folder' : String(category ?? ''), slot: 0 };
}

// Folder that holds an item (Windows or POSIX paths), as written in the item's path
function parentFolder(path) {
  const text = String(path ?? '').replace(/[\\/]+$/, '');
  const cut = Math.max(text.lastIndexOf('\\'), text.lastIndexOf('/'));
  if (cut < 0) return '';
  const parent = text.slice(0, cut);
  // Keep the separator on a drive or filesystem root ("C:\" or "/")
  return /^[A-Za-z]:$/.test(parent) || parent === '' ? text.slice(0, cut + 1) : parent;
}

// Results grouped by the folder they are in, biggest first (count breaks ties)
function junkByFolder(items, limit = 50) {
  const groups = new Map();
  items.forEach((item) => {
    const folder = parentFolder(item.path);
    const key = folder.toLowerCase();
    const group = groups.get(key) || { folder, bytes: 0, count: 0 };
    group.bytes += Number(item.size_bytes) || 0;
    group.count += 1;
    groups.set(key, group);
  });
  return [...groups.values()]
    .sort((a, b) => b.bytes - a.bytes || b.count - a.count || a.folder.localeCompare(b.folder))
    .slice(0, limit);
}

// Smart score bands (match app/engine/smart.py)
const SCORE_DELETE = 75;
const SCORE_REVIEW = 45;

function scoreBand(score) {
  if (score >= SCORE_DELETE) return 'delete';
  if (score >= SCORE_REVIEW) return 'review';
  return 'keep';
}

// "Large file ..." -> "large file ...", but acronyms such as "OS" stay as they are
function lowerFirst(text) {
  return /^[A-Z][a-z]/.test(text) ? text.charAt(0).toLowerCase() + text.slice(1) : text;
}

// Plain-language summary of a scan, built from the smart scores. fmt formats a byte count.
function smartSummary(items, fmt) {
  const sum = (list) => list.reduce((acc, i) => acc + (Number(i.size_bytes) || 0), 0);
  const plural = (n, word) => `${n.toLocaleString()} ${word}${n === 1 ? '' : 's'}`;
  const verb = (list, one, many) => (list.length === 1 ? one : many);
  const bands = { delete: [], review: [], keep: [] };
  // Items from reports saved before smart scores existed have no score and are left out
  items.filter((i) => Number(i.score) > 0).forEach((i) => bands[scoreBand(Number(i.score))].push(i));
  const lines = [];

  if (bands.delete.length) {
    // The category holding most of the confident space (or most items, if they are all empty)
    const byCat = {};
    bands.delete.forEach((i) => {
      const c = byCat[i.category] || (byCat[i.category] = { bytes: 0, count: 0 });
      c.bytes += Number(i.size_bytes) || 0;
      c.count += 1;
    });
    const [topName] = Object.entries(byCat).sort((a, b) => (b[1].bytes - a[1].bytes) || (b[1].count - a[1].count))[0];
    const top = SPACE_CATEGORIES.find((c) => c.name === topName);
    lines.push({ band: 'delete', text: `${plural(bands.delete.length, 'item')} (${fmt(sum(bands.delete))}) can go now with little risk, mostly ${(top ? top.label : topName).toLowerCase()}.` });
  } else if (items.length) {
    lines.push({ band: 'delete', text: 'Nothing here is a sure bet, so look before you delete.' });
  }

  if (bands.review.length) {
    const biggest = bands.review.reduce((a, b) => ((Number(b.size_bytes) || 0) > (Number(a.size_bytes) || 0) ? b : a));
    lines.push({ band: 'review', text: `${plural(bands.review.length, 'item')} (${fmt(sum(bands.review))}) ${verb(bands.review, 'is', 'are')} worth a look first. The biggest is ${biggest.name} (${fmt(Number(biggest.size_bytes) || 0)}): ${lowerFirst(String(biggest.reason || ''))}.` });
  }

  if (bands.keep.length) {
    const kept = bands.keep.filter((i) => (i.score_reasons || []).some((r) => r.includes('you kept'))).length;
    const note = kept ? `, including ${plural(kept, 'item')} you kept after earlier scans` : '';
    lines.push({ band: 'keep', text: `${plural(bands.keep.length, 'item')} (${fmt(sum(bands.keep))}) ${verb(bands.keep, 'looks', 'look')} worth keeping${note}.` });
  }
  return { lines, deleteCount: bands.delete.length, deleteBytes: sum(bands.delete) };
}

// Lines for the delete confirm dialog, from /api/delete-preview: what needs a second look first
function deletePreviewLines(summary, trashName = 'Recycle Bin') {
  const lines = [];
  const plural = (n, word) => `${n.toLocaleString()} ${word}${n === 1 ? '' : 's'}`;
  if (summary.personal_count) {
    const examples = (summary.personal_examples || []).join(', ');
    lines.push({ level: 'warning', text: `${plural(summary.personal_count, 'personal file')} (documents, photos, videos or saves)${examples ? `, such as ${examples}` : ''}` });
  }
  Object.entries(summary.cloud || {}).forEach(([provider, n]) => {
    lines.push({ level: 'warning', text: `${plural(n, 'item')} in ${provider}: deleting also removes ${n === 1 ? 'it' : 'them'} from the cloud and your other devices` });
  });
  if (summary.no_recycle_count) {
    lines.push({ level: 'info', text: `${plural(summary.no_recycle_count, 'item')} on a drive without a ${trashName} (USB or network): JunkZero keeps ${summary.no_recycle_count === 1 ? 'it' : 'them'} in a hidden folder on that drive for 7 days, and you can restore ${summary.no_recycle_count === 1 ? 'it' : 'them'} from Cleanup History` });
  }
  if (summary.truncated) {
    lines.push({ level: 'warning', text: 'Too many files inside to check them all' });
  }
  if (summary.needs_typed_confirm && !lines.some((l) => l.level === 'warning')) {
    lines.push({ level: 'warning', text: `This is an unusually large delete (${plural(summary.count, 'item')}, ${summary.bytes_formatted})` });
  }
  if (summary.count > 1) {
    (summary.top_folders || []).slice(0, 3).forEach((f) => {
      lines.push({ level: 'info', text: `From ${f.folder}: ${plural(f.count, 'item')}, ${f.bytes_formatted}` });
    });
  }
  if ((summary.types || []).length) {
    lines.push({ level: 'info', text: `Types: ${summary.types.map((t) => `${t.type} ${t.count.toLocaleString()}`).join(', ')}` });
  }
  return lines;
}

// Exclusion rules, as app/engine/exclusions.py matches them: a rule with *, ? or [ is a pattern
// (matched against the name and the full path); any other rule is a path that covers that file
// or folder and everything inside it. Case-insensitive, either kind of slash.
function normRulePath(p) {
  return String(p ?? '').replace(/\\/g, '/').replace(/\/+$/, '').toLowerCase();
}

function globToRegExp(glob) {
  let re = '';
  for (let i = 0; i < glob.length; i += 1) {
    const c = glob[i];
    if (c === '*') re += '.*';
    else if (c === '?') re += '.';
    else if (c === '[') {
      const end = glob.indexOf(']', i + 2);
      if (end < 0) { re += '\\['; continue; }
      let cls = glob.slice(i + 1, end);
      if (cls[0] === '!') cls = `^${cls.slice(1)}`;
      re += `[${cls.replace(/\\/g, '\\\\')}]`;
      i = end;
    } else re += c.replace(/[.+^${}()|\\/\]]/g, '\\$&');
  }
  return new RegExp(`^${re}$`, 's');
}

// The first rule (as written) that excludes path, or null
function exclusionRuleFor(path, rules) {
  const norm = normRulePath(path);
  const name = norm.slice(norm.lastIndexOf('/') + 1);
  for (const rule of rules || []) {
    const r = String(rule ?? '').trim();
    if (!r || /[*?[]/.test(r)) continue;
    const p = normRulePath(r);
    if (p && (norm === p || norm.startsWith(`${p}/`))) return r;
  }
  for (const rule of rules || []) {
    const r = String(rule ?? '').trim();
    if (!r || !/[*?[]/.test(r)) continue;
    const re = globToRegExp(normRulePath(r));
    if (re.test(name) || re.test(norm)) return r;
  }
  return null;
}

// Same folder, ignoring case and a trailing separator ("C:\\" and "c:", "/Users/me/" and "/Users/me")
function samePath(a, b) {
  const norm = (p) => {
    const text = String(p ?? '').trim().toLowerCase();
    const stripped = text.replace(/[\\/]+$/, '');
    return stripped === '' && text !== '' ? '/' : stripped;
  };
  return norm(a) !== '' && norm(a) === norm(b);
}

// File type search input ("mp4, .MOV *.mkv tar.gz") -> lowercase types without the dot.
// Mirrors app/engine/filetypes.py; the server checks again and has the last word.
const FILE_TYPE_PATTERN = /^[a-z0-9_+~$-]{1,16}(\.[a-z0-9_+~$-]{1,16}){0,2}$/;

function parseFileTypes(text) {
  const types = [];
  const invalid = [];
  String(text ?? '').split(/[\s,;]+/).forEach((raw) => {
    if (!raw) return;
    let value = raw.toLowerCase();
    if (value.replace(/[*.]/g, '') === '' && value.includes('*')) { invalid.push(raw); return; }
    value = value.replace(/^\*/, '').replace(/^\.+/, '');
    if (!value) return;
    if (!FILE_TYPE_PATTERN.test(value)) { invalid.push(raw); return; }
    if (!types.includes(value)) types.push(value);
  });
  return { types, invalid };
}

// Whether a preset group is fully, partly or not at all in the chosen types (for aria-pressed)
function presetState(presetTypes, chosen) {
  const picked = presetTypes.filter((t) => chosen.includes(t)).length;
  if (picked === 0) return 'false';
  return picked === presetTypes.length ? 'true' : 'mixed';
}

// The path is the folder itself or something inside it ("C:\foo\bar2" is not inside "C:\foo\bar")
function pathIsWithin(path, root) {
  const norm = (p) => String(p ?? '').toLowerCase().replace(/\\/g, '/').replace(/\/+$/, '');
  const a = norm(path);
  const b = norm(root);
  return b !== '' && (a === b || a.startsWith(`${b}/`));
}

// The scan's current step (ScanStats.phase on the server) in plain words; '' when unknown
const SCAN_PHASE_LABELS = {
  walking: 'Scanning folders',
  leftovers: 'Checking program leftovers',
  empty_folders: 'Looking for empty folders',
  duplicates: 'Comparing duplicates',
  done: 'Finishing up',
};

function scanPhaseLabel(phase) {
  return SCAN_PHASE_LABELS[phase] || '';
}

// "Deleting 1,200 of 3,000" while a big delete runs (from /api/clean/progress)
function deleteProgressText(progress) {
  const total = Number(progress?.total) || 0;
  const done = Math.min(Number(progress?.done) || 0, total);
  if (!progress?.running || total <= 0) return 'Deleting...';
  return `Deleting ${done.toLocaleString('en-US')} of ${total.toLocaleString('en-US')}`;
}

function deleteProgressPercent(progress) {
  const total = Number(progress?.total) || 0;
  if (!progress?.running || total <= 0) return null; // unknown: the bar stays indeterminate
  return Math.max(0, Math.min(100, ((Number(progress.done) || 0) / total) * 100));
}

// The error list as plain text for the clipboard: one "path<TAB>reason" line per item
function errorListText(errors) {
  return (errors || []).map((e) => `${e.path ?? ''}\t${e.error ?? ''}`).join('\n');
}

// What to say after "Check for updates" (/api/update/check)
function updateCheckMessage(result) {
  if (!result) return { text: 'Could not check for updates.', url: '' };
  if (result.error) return { text: `Could not check for updates: ${result.error}`, url: '' };
  if (result.newer && result.latest) {
    return { text: `JunkZero ${result.latest} is available (you have ${result.current}).`, url: result.url || '' };
  }
  return { text: `You have the latest version (${result.current}).`, url: '' };
}

// Index to move focus to when Tab (or Shift+Tab) leaves the ends of a dialog; -1 = let it be
function trappedFocusIndex(count, current, backwards) {
  if (count <= 0) return -1;
  if (current < 0) return backwards ? count - 1 : 0;
  if (backwards && current === 0) return count - 1;
  if (!backwards && current === count - 1) return 0;
  return -1;
}

/**
 * JunkZero - Frontend Application Controller
 * High-performance file management, live scan streaming, multi-level folder hierarchy exploration,
 * and Recycle Bin (default) or permanent deletion.
 */

// Icons are bundled in /static/vendor; if the script fails to load the app must still work, just without icons
if (!window.lucide) window.lucide = { createIcons() {} };

// The table shows this many rows at a time ("Show more" adds another page), so large scans stay responsive
const TABLE_PAGE_SIZE = 500;
// While a scan runs, the results table is redrawn at most this often, so found items show
// up right away without the table redrawing on every single item
const LIVE_RENDER_MS = 500;
const SEARCH_DELAY_MS = 200;
// While a delete runs, its progress is asked for this often
const CLEAN_PROGRESS_MS = 300;
// When the live scan stream drops, the scan's status is asked for this often until it ends
const SCAN_STATUS_POLL_MS = 1000;

// Every API request carries this launch's app token (the server refuses it otherwise). JunkZero
// opens its window at /#token=..., so the token never sits in the page itself. It is kept for
// this window only, so a reload still works, and taken out of the address bar.
const APP_TOKEN = (() => {
  let token = new URLSearchParams(location.hash.slice(1)).get('token') || '';
  try {
    if (token) sessionStorage.setItem('junkzero-token', token);
    else token = sessionStorage.getItem('junkzero-token') || '';
  } catch { /* storage blocked: the token still works until the page is reloaded */ }
  if (location.hash) history.replaceState(null, '', location.pathname + location.search);
  return token;
})();
const nativeFetch = window.fetch.bind(window);
window.fetch = (input, init = {}) => {
  const url = typeof input === 'string' ? input : input.url;
  if (url.startsWith('/api/')) {
    const headers = new Headers(init.headers || {});
    headers.set('X-JunkZero-Token', APP_TOKEN);
    init = { ...init, headers };
  }
  return nativeFetch(input, init);
};

// Global State
const state = {
  items: [],              // All scanned GarbageItems
  filteredItems: [],      // Items currently visible after search & filter
  renderLimit: TABLE_PAGE_SIZE, // Rows of filteredItems currently rendered in the table
  selectedIds: new Set(), // Set of selected item IDs in main table
  stats: {
    totalBytes: 0,
    totalCount: 0,
    categoryCounts: {},
    categoryBytes: {},
  },
  drives: [],
  platform: 'windows',    // 'windows', 'macos' or 'linux' (from /api/system/info)
  isScanning: false,
  eventSource: null,
  sortField: 'size',
  sortAsc: false,
  categoryFilter: 'ALL',
  riskFilter: 'ALL',
  searchQuery: '',
  folderFilter: null,     // Lower-cased folder from "By Folder", or null for all
  newPaths: new Set(),    // Paths the previous scan of this target did not find
  newOnly: false,
  expandedScoreId: null,  // Row whose smart-score reasons are shown
  fileTypes: [],          // File type search: lowercase extensions without the dot
  fileTypeMinDays: 0,     // ...only files unchanged for this many days (0 = any age)
  fileTypePresets: [],    // Groups from /api/file-types/presets
  fileTypesNeverListed: [], // Backup images and VM disks: the search refuses these
  typeSearchOnly: false,  // The running/last scan looked only for the chosen file types
  liveRenderTimer: null,  // Pending table redraw while a scan is running
  exclusionRules: [],     // The Exclusions list: excluded items can't be ticked or deleted
  version: '',            // App version (from /api/system/info)
  scanRun: 0,             // Counts scans, so a late answer about an old scan is ignored
  isDeleting: false,      // A delete is running: the window asks before closing
  cleanJob: null,         // The running /api/clean progress poll
  shownErrors: [],        // What the errors dialog lists (for "Copy")

  // Hierarchy Explorer State
  currentHierarchy: null,
  hierarchySelectedPaths: new Set(),
  initialTargetFilePath: null,
  deleteConfirmCallback: null,

  // Delete mode: Recycle Bin unless the user explicitly switches to permanent.
  // Deliberately not persisted, so every launch starts in the safe mode.
  permanentDelete: false,
};

// DOM Element References
const el = {
  themeToggle: document.getElementById('themeToggle'),
  themeIcon: document.getElementById('themeIcon'),
  engineStatus: document.getElementById('engineStatus'),
  statusText: document.getElementById('statusText'),
  driveButtons: document.getElementById('driveButtons'),
  targetPathInput: document.getElementById('targetPathInput'),
  btnBrowse: document.getElementById('btnBrowse'),

  // Option Checkboxes
  optInstallers: document.getElementById('optInstallers'),
  optJavaBuilds: document.getElementById('optJavaBuilds'),
  optTemp: document.getElementById('optTemp'),
  optDownloads: document.getElementById('optDownloads'),
  optStaleLarge: document.getElementById('optStaleLarge'),
  optEmptyFolders: document.getElementById('optEmptyFolders'),
  optDuplicates: document.getElementById('optDuplicates'),
  optOldDownloads: document.getElementById('optOldDownloads'),

  // Primary Buttons
  btnStartScan: document.getElementById('btnStartScan'),
  btnStopScan: document.getElementById('btnStopScan'),
  btnScanJunk: document.getElementById('btnScanJunk'),
  btnLargest: document.getElementById('btnLargest'),
  btnSelectAll: document.getElementById('btnSelectAll'),
  btnSelectAllSafe: document.getElementById('btnSelectAllSafe'),
  btnSmartSelect: document.getElementById('btnSmartSelect'),
  btnSmartSelectSummary: document.getElementById('btnSmartSelectSummary'),
  smartSummary: document.getElementById('smartSummary'),
  smartSummaryList: document.getElementById('smartSummaryList'),
  learningNote: document.getElementById('learningNote'),
  btnForgetLearning: document.getElementById('btnForgetLearning'),
  optLeftovers: document.getElementById('optLeftovers'),
  optFileTypes: document.getElementById('optFileTypes'),
  fileTypesHint: document.getElementById('fileTypesHint'),
  btnChooseFileTypes: document.getElementById('btnChooseFileTypes'),

  // File Types Modal
  btnOpenFileTypes: document.getElementById('btnOpenFileTypes'),
  fileTypesModal: document.getElementById('fileTypesModal'),
  closeFileTypesModal: document.getElementById('closeFileTypesModal'),
  fileTypePresets: document.getElementById('fileTypePresets'),
  fileTypeInput: document.getElementById('fileTypeInput'),
  btnAddFileType: document.getElementById('btnAddFileType'),
  fileTypeChips: document.getElementById('fileTypeChips'),
  fileTypeCount: document.getElementById('fileTypeCount'),
  fileTypeMinDays: document.getElementById('fileTypeMinDays'),
  btnClearFileTypes: document.getElementById('btnClearFileTypes'),
  btnCloseFileTypes: document.getElementById('btnCloseFileTypes'),
  btnSearchFileTypes: document.getElementById('btnSearchFileTypes'),
  btnDeselectAll: document.getElementById('btnDeselectAll'),
  btnExportReport: document.getElementById('btnExportReport'),
  btnDeleteItems: document.getElementById('btnDeleteItems'),
  modeRecycle: document.getElementById('modeRecycle'),
  modePermanent: document.getElementById('modePermanent'),

  // Progress UI
  scanProgressContainer: document.getElementById('scanProgressContainer'),
  scanProgressText: document.getElementById('scanProgressText'),
  scanMetricsFiles: document.getElementById('scanMetricsFiles'),
  scanMetricsDirs: document.getElementById('scanMetricsDirs'),
  scanMetricsRate: document.getElementById('scanMetricsRate'),

  // Stat Counters
  statTotalGarbageSize: document.getElementById('statTotalGarbageSize'),
  statTotalGarbageCount: document.getElementById('statTotalGarbageCount'),
  statInstallersSize: document.getElementById('statInstallersSize'),
  statInstallersCount: document.getElementById('statInstallersCount'),
  statJavaSize: document.getElementById('statJavaSize'),
  statJavaCount: document.getElementById('statJavaCount'),
  statTempSize: document.getElementById('statTempSize'),
  statTempCount: document.getElementById('statTempCount'),
  statDownloadsSize: document.getElementById('statDownloadsSize'),
  statDownloadsCount: document.getElementById('statDownloadsCount'),
  statEmptyFoldersCount: document.getElementById('statEmptyFoldersCount'),
  statStaleSize: document.getElementById('statStaleSize'),
  statStaleCount: document.getElementById('statStaleCount'),
  statDuplicatesSize: document.getElementById('statDuplicatesSize'),
  statDuplicatesCount: document.getElementById('statDuplicatesCount'),
  statOldDownloadsSize: document.getElementById('statOldDownloadsSize'),
  statOldDownloadsCount: document.getElementById('statOldDownloadsCount'),

  // Space Breakdown
  spaceBreakdown: document.getElementById('spaceBreakdown'),
  spaceBreakdownTotal: document.getElementById('spaceBreakdownTotal'),
  spaceBar: document.getElementById('spaceBar'),
  spaceLegend: document.getElementById('spaceLegend'),

  // Table & Toolbar
  searchInput: document.getElementById('searchInput'),
  clearSearch: document.getElementById('clearSearch'),
  categoryFilter: document.getElementById('categoryFilter'),
  riskFilter: document.getElementById('riskFilter'),
  fileTableBody: document.getElementById('fileTableBody'),
  masterCheckbox: document.getElementById('masterCheckbox'),
  selectedCount: document.getElementById('selectedCount'),
  selectedSize: document.getElementById('selectedSize'),
  hiddenSelectedNote: document.getElementById('hiddenSelectedNote'),

  // Folder Hierarchy Explorer Modal
  hierarchyModal: document.getElementById('hierarchyModal'),
  closeHierarchyModal: document.getElementById('closeHierarchyModal'),
  btnCloseHierarchy: document.getElementById('btnCloseHierarchy'),
  hierarchyBreadcrumbs: document.getElementById('hierarchyBreadcrumbs'),
  btnUpLevel: document.getElementById('btnUpLevel'),
  hierarchyFolderName: document.getElementById('hierarchyFolderName'),
  hierarchyFolderMeta: document.getElementById('hierarchyFolderMeta'),
  btnDeleteThisFolder: document.getElementById('btnDeleteThisFolder'),
  hierarchyTableBody: document.getElementById('hierarchyTableBody'),
  hierarchyMasterCheck: document.getElementById('hierarchyMasterCheck'),
  hierarchySelectedSummary: document.getElementById('hierarchySelectedSummary'),
  btnDeleteInitialFile: document.getElementById('btnDeleteInitialFile'),
  btnDeleteHierarchySelected: document.getElementById('btnDeleteHierarchySelected'),

  // AI Modal
  aiModal: document.getElementById('aiModal'),
  aiModalBody: document.getElementById('aiModalBody'),
  closeAiModal: document.getElementById('closeAiModal'),
  btnAiClose: document.getElementById('btnAiClose'),

  // Confirm Modal
  confirmModal: document.getElementById('confirmModal'),
  confirmModalTitle: document.getElementById('confirmModalTitle'),
  confirmModalSubtitle: document.getElementById('confirmModalSubtitle'),
  confirmWarningText: document.getElementById('confirmWarningText'),
  confirmCount: document.getElementById('confirmCount'),
  confirmSize: document.getElementById('confirmSize'),
  confirmMode: document.getElementById('confirmMode'),
  confirmModalIcon: document.getElementById('confirmModalIcon'),
  confirmWarningBanner: document.getElementById('confirmWarningBanner'),
  confirmPermanentAck: document.getElementById('confirmPermanentAck'),
  confirmPermanentAckInput: document.getElementById('confirmPermanentAckInput'),
  confirmChecks: document.getElementById('confirmChecks'),
  confirmTyped: document.getElementById('confirmTyped'),
  confirmTypedInput: document.getElementById('confirmTypedInput'),
  closeConfirmModal: document.getElementById('closeConfirmModal'),
  btnCancelDelete: document.getElementById('btnCancelDelete'),
  btnExecuteDelete: document.getElementById('btnExecuteDelete'),

  // Exclusions Modal
  btnOpenExclusions: document.getElementById('btnOpenExclusions'),
  exclusionsModal: document.getElementById('exclusionsModal'),
  closeExclusionsModal: document.getElementById('closeExclusionsModal'),
  btnCloseExclusions: document.getElementById('btnCloseExclusions'),
  exclusionInput: document.getElementById('exclusionInput'),
  btnAddExclusion: document.getElementById('btnAddExclusion'),
  exclusionsList: document.getElementById('exclusionsList'),

  // Junk Rules Modal
  btnOpenJunkRules: document.getElementById('btnOpenJunkRules'),
  junkRulesModal: document.getElementById('junkRulesModal'),
  closeJunkRulesModal: document.getElementById('closeJunkRulesModal'),
  btnCloseJunkRules: document.getElementById('btnCloseJunkRules'),
  junkRuleInput: document.getElementById('junkRuleInput'),
  btnAddJunkRule: document.getElementById('btnAddJunkRule'),
  junkRulesList: document.getElementById('junkRulesList'),

  // New since last scan & Junk by Folder
  btnNewOnly: document.getElementById('btnNewOnly'),
  newOnlyLabel: document.getElementById('newOnlyLabel'),
  btnByFolder: document.getElementById('btnByFolder'),
  folderFilterChip: document.getElementById('folderFilterChip'),
  folderFilterText: document.getElementById('folderFilterText'),
  clearFolderFilter: document.getElementById('clearFolderFilter'),
  byFolderModal: document.getElementById('byFolderModal'),
  byFolderSubtitle: document.getElementById('byFolderSubtitle'),
  byFolderList: document.getElementById('byFolderList'),
  closeByFolderModal: document.getElementById('closeByFolderModal'),
  btnCloseByFolder: document.getElementById('btnCloseByFolder'),

  // History Modal
  btnOpenHistory: document.getElementById('btnOpenHistory'),
  historyModal: document.getElementById('historyModal'),
  closeHistoryModal: document.getElementById('closeHistoryModal'),
  btnCloseHistory: document.getElementById('btnCloseHistory'),
  historySummary: document.getElementById('historySummary'),
  historyList: document.getElementById('historyList'),
  btnClearHistory: document.getElementById('btnClearHistory'),
  btnOpenRecycleBin: document.getElementById('btnOpenRecycleBin'),

  // Schedule Modal
  btnOpenSchedule: document.getElementById('btnOpenSchedule'),
  scheduleModal: document.getElementById('scheduleModal'),
  closeScheduleModal: document.getElementById('closeScheduleModal'),
  scheduleStatus: document.getElementById('scheduleStatus'),
  scheduleFrequency: document.getElementById('scheduleFrequency'),
  scheduleDay: document.getElementById('scheduleDay'),
  scheduleDayLabel: document.getElementById('scheduleDayLabel'),
  scheduleTime: document.getElementById('scheduleTime'),
  schedulePaths: document.getElementById('schedulePaths'),
  scheduleJunk: document.getElementById('scheduleJunk'),
  lastReportBox: document.getElementById('lastReportBox'),
  lastReportText: document.getElementById('lastReportText'),
  btnLoadReport: document.getElementById('btnLoadReport'),
  btnSaveSchedule: document.getElementById('btnSaveSchedule'),
  btnDisableSchedule: document.getElementById('btnDisableSchedule'),

  // Largest Files Modal
  largestModal: document.getElementById('largestModal'),
  largestSubtitle: document.getElementById('largestSubtitle'),
  largestList: document.getElementById('largestList'),
  largestTabFolders: document.getElementById('largestTabFolders'),
  largestTabFiles: document.getElementById('largestTabFiles'),
  closeLargestModal: document.getElementById('closeLargestModal'),
  btnCloseLargest: document.getElementById('btnCloseLargest'),

  toastContainer: document.getElementById('toastContainer'),

  // Version, updates and diagnostics
  appVersion: document.getElementById('appVersion'),
  btnOpenAbout: document.getElementById('btnOpenAbout'),
  aboutModal: document.getElementById('aboutModal'),
  aboutVersion: document.getElementById('aboutVersion'),
  closeAboutModal: document.getElementById('closeAboutModal'),
  btnCloseAbout: document.getElementById('btnCloseAbout'),
  btnCheckUpdates: document.getElementById('btnCheckUpdates'),
  btnCopyDiagnostics: document.getElementById('btnCopyDiagnostics'),
  updateResult: document.getElementById('updateResult'),

  // Errors dialog
  errorsModal: document.getElementById('errorsModal'),
  errorsModalTitle: document.getElementById('errorsModalTitle'),
  errorsModalSubtitle: document.getElementById('errorsModalSubtitle'),
  errorsList: document.getElementById('errorsList'),
  closeErrorsModal: document.getElementById('closeErrorsModal'),
  btnCloseErrors: document.getElementById('btnCloseErrors'),
  btnCopyErrors: document.getElementById('btnCopyErrors'),

  // Delete progress (inside the confirm dialog)
  deleteProgress: document.getElementById('deleteProgress'),
  deleteProgressText: document.getElementById('deleteProgressText'),
  deleteProgressBar: document.getElementById('deleteProgressBar'),
  btnCancelClean: document.getElementById('btnCancelClean'),

  // Windows cleanup that needs admin
  cleanupHints: document.getElementById('cleanupHints'),
  cleanupHintsList: document.getElementById('cleanupHintsList'),
  cleanupHintsTotal: document.getElementById('cleanupHintsTotal'),
};

// Scan toggles remembered between launches (request field -> checkbox)
const SCAN_OPTION_INPUTS = {
  include_installers: el.optInstallers,
  include_java_builds: el.optJavaBuilds,
  include_temp_junk: el.optTemp,
  include_broken_downloads: el.optDownloads,
  include_stale_large: el.optStaleLarge,
  include_empty_folders: el.optEmptyFolders,
  include_duplicates: el.optDuplicates,
  include_old_downloads: el.optOldDownloads,
  include_leftovers: el.optLeftovers,
  include_file_types: el.optFileTypes,
};

// Utilities
function plural(count, word) {
  const n = Number(count) || 0;
  return `${n.toLocaleString()} ${word}${n === 1 ? '' : 's'}`;
}

function formatSize(bytes) {
  if (!bytes || bytes <= 0) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let b = bytes;
  for (let i = 0; i < units.length; i++) {
    if (b < 1024.0 || i === units.length - 1) {
      return (i === 0 ? b : b.toFixed(2)) + ' ' + units[i];
    }
    b /= 1024.0;
  }
  return b.toFixed(2) + ' TB';
}

// Errors stay on screen until dismissed, so they can't vanish before they are read. `details`
// ([{path, error}]) adds "Show details", which lists every item and its reason.
function showToast(message, type = 'info', { details = null, detailsTitle = '' } = {}) {
  const sticky = type === 'error';
  const toast = document.createElement('div');
  toast.className = `toast toast-${type}${sticky ? ' toast-sticky' : ''}`;
  const icon = { success: 'circle-check', error: 'circle-alert' }[type] || 'info';
  toast.innerHTML = `<i data-lucide="${icon}" class="toast-icon"></i><div class="toast-text"><span></span></div>`;
  toast.querySelector('span').textContent = message;
  if (details && details.length) {
    const more = document.createElement('button');
    more.type = 'button';
    more.className = 'toast-action';
    more.textContent = 'Show details';
    more.addEventListener('click', () => openErrorsModal(details, detailsTitle));
    toast.querySelector('.toast-text').appendChild(more);
  }
  if (sticky) {
    const close = document.createElement('button');
    close.type = 'button';
    close.className = 'toast-close';
    close.setAttribute('aria-label', 'Dismiss');
    close.title = 'Dismiss';
    close.innerHTML = '&times;';
    close.addEventListener('click', () => toast.remove());
    toast.appendChild(close);
  }
  el.toastContainer.appendChild(toast);
  lucide.createIcons({ root: toast });
  if (!sticky) {
    setTimeout(() => {
      toast.style.opacity = '0';
      setTimeout(() => toast.remove(), 200);
    }, 3500);
  }
}

// Every item that couldn't be removed (or restored), with its reason and a Copy button
function openErrorsModal(errors, title = '') {
  state.shownErrors = errors || [];
  el.errorsModalTitle.textContent = title || "Items that couldn't be removed";
  el.errorsModalSubtitle.textContent = `${plural(state.shownErrors.length, 'item')}; the reason is under each path`;
  el.errorsList.innerHTML = state.shownErrors.map((e) => `
    <li><span>${escapeHtml(e.path)}</span><span class="error-reason">${escapeHtml(e.error)}</span></li>
  `).join('');
  el.errorsModal.classList.remove('hidden');
}

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    return;
  } catch { /* no clipboard access here: fall back to a hidden text box */ }
  const box = document.createElement('textarea');
  box.value = text;
  box.setAttribute('readonly', '');
  box.style.position = 'fixed';
  box.style.opacity = '0';
  document.body.appendChild(box);
  box.select();
  const ok = document.execCommand('copy');
  box.remove();
  if (!ok) throw new Error('the clipboard is not available');
}

// Dialogs: Escape closes the top one, Tab stays inside it, and focus goes back where it was
const FOCUSABLE = 'button:not([disabled]), [href], input:not([disabled]):not([type="hidden"]), '
  + 'select:not([disabled]), textarea:not([disabled]), summary, [tabindex]:not([tabindex="-1"])';

function topDialog() {
  const open = [...document.querySelectorAll('.modal-overlay:not(.hidden)')];
  return open[open.length - 1] || null;
}

function setupDialogs() {
  const recentFocus = [];
  document.addEventListener('focusin', (e) => {
    recentFocus.push(e.target);
    if (recentFocus.length > 10) recentFocus.shift();
  });
  document.querySelectorAll('.modal-overlay').forEach((overlay) => {
    const card = overlay.querySelector('[role="dialog"]') || overlay;
    card.setAttribute('tabindex', '-1');
    let opener = null;
    new MutationObserver(() => {
      const open = !overlay.classList.contains('hidden');
      if (open && !overlay.dataset.open) {
        overlay.dataset.open = '1';
        opener = [...recentFocus].reverse().find((n) => n.isConnected && !overlay.contains(n)) || null;
        if (!overlay.contains(document.activeElement)) card.focus();
      } else if (!open && overlay.dataset.open) {
        delete overlay.dataset.open;
        if (opener && opener.isConnected && opener.offsetParent !== null) opener.focus();
        opener = null;
      }
    }).observe(overlay, { attributes: true, attributeFilter: ['class'] });
  });
  document.addEventListener('keydown', (e) => {
    const overlay = topDialog();
    if (!overlay) return;
    if (e.key === 'Escape') {
      e.preventDefault();
      overlay.querySelector('.close-modal-btn')?.click();
    } else if (e.key === 'Tab') {
      const items = [...overlay.querySelectorAll(FOCUSABLE)].filter((n) => n.offsetParent !== null);
      const inside = overlay.contains(document.activeElement);
      const next = inside ? trappedFocusIndex(items.length, items.indexOf(document.activeElement), e.shiftKey)
        : (e.shiftKey ? items.length - 1 : 0);
      if (next >= 0 && items[next]) {
        e.preventDefault();
        items[next].focus();
      }
    }
  });
}

// A problem with how JunkZero was installed: stays on screen until dismissed
function showSetupWarning(message) {
  const toast = document.createElement('div');
  toast.className = 'toast toast-error toast-sticky';
  toast.setAttribute('role', 'alert');
  toast.innerHTML = '<i data-lucide="circle-alert" class="toast-icon"></i><span></span>'
    + '<button type="button" class="toast-close" aria-label="Dismiss" title="Dismiss">&times;</button>';
  toast.querySelector('span').textContent = message;
  toast.querySelector('.toast-close').addEventListener('click', () => toast.remove());
  el.toastContainer.appendChild(toast);
  lucide.createIcons({ root: toast });
}

// App Initialization
async function initApp() {
  await loadPlatform();
  lucide.createIcons();
  setupEventListeners();
  setupDialogs();
  if (!APP_TOKEN) {
    showToast('This page was opened without JunkZero. Start JunkZero to scan and clean.', 'error');
  }
  await loadAvailableDrives();
  await loadPreferences();
  await loadExclusionRules();
  loadCleanupHints();
}

// Event Listeners
function setupEventListeners() {
  bindTableEvents();

  // Theme Toggle
  el.themeToggle.addEventListener('click', () => {
    const isDark = document.body.classList.toggle('dark-theme');
    document.body.classList.toggle('light-theme', !isDark);
    el.themeIcon.setAttribute('data-lucide', isDark ? 'sun' : 'moon');
    lucide.createIcons();
    localStorage.setItem('junkzero_theme', isDark ? 'dark' : 'light');
    savePreferences();
  });

  const savedTheme = localStorage.getItem('junkzero_theme');
  if (savedTheme === 'light') {
    document.body.classList.remove('dark-theme');
    document.body.classList.add('light-theme');
    el.themeIcon.setAttribute('data-lucide', 'moon');
  }

  // Browse Folder Button
  el.btnBrowse.addEventListener('click', async () => {
    try {
      el.btnBrowse.disabled = true;
      el.btnBrowse.innerHTML = '<div class="spinner"></div> Opening...';
      const res = await fetch('/api/system/browse-folder', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ start: el.targetPathInput.value.trim() }),
      });
      const data = await res.json();
      if (data.path) {
        el.targetPathInput.value = data.path;
        syncDrivePills();
        savePreferences();
        showToast(`Target set to: ${data.path}`, 'success');
      } else if (data.error) {
        showToast(data.error, 'error');
      }
    } catch (e) {
      showToast('Could not open folder picker', 'error');
    } finally {
      el.btnBrowse.disabled = false;
      el.btnBrowse.innerHTML = '<i data-lucide="folder-open"></i> Browse';
      lucide.createIcons();
    }
  });

  // A typed path that isn't a whole drive no longer leaves the drive highlighted
  el.targetPathInput.addEventListener('input', syncDrivePills);
  // Enter in the path box starts a scan, like the Scan button
  el.targetPathInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !state.isScanning) {
      e.preventDefault();
      startScan();
    }
  });

  // Start / Stop Scan Buttons
  el.btnStartScan.addEventListener('click', () => startScan());
  el.btnScanJunk.addEventListener('click', () => startScan({ junkOnly: true }));
  el.btnLargest.addEventListener('click', openLargestModal);
  el.btnOpenFileTypes.addEventListener('click', openFileTypesModal);
  el.btnChooseFileTypes.addEventListener('click', openFileTypesModal);
  el.closeFileTypesModal.addEventListener('click', closeFileTypesModal);
  el.btnCloseFileTypes.addEventListener('click', closeFileTypesModal);
  el.btnAddFileType.addEventListener('click', () => addFileTypes(el.fileTypeInput.value));
  el.fileTypeInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') addFileTypes(el.fileTypeInput.value);
  });
  el.btnClearFileTypes.addEventListener('click', () => setFileTypes([]));
  el.fileTypeMinDays.addEventListener('change', () => {
    const days = Math.min(36500, Math.max(0, Math.floor(Number(el.fileTypeMinDays.value) || 0)));
    el.fileTypeMinDays.value = String(days);
    state.fileTypeMinDays = days;
    savePreferences();
  });
  el.btnSearchFileTypes.addEventListener('click', () => {
    if (state.isScanning) {
      showToast('A scan is running. Wait for it to finish or stop it first.', 'error');
      return;
    }
    if (!state.fileTypes.length) {
      showToast('Pick at least one file type first', 'error');
      el.fileTypeInput.focus();
      return;
    }
    closeFileTypesModal();
    startScan({ typeOnly: true });
  });
  Object.values(SCAN_OPTION_INPUTS).forEach((input) => input.addEventListener('change', savePreferences));
  el.btnStopScan.addEventListener('click', stopScan);

  // Search & Filter Listeners
  let searchTimer = null;
  el.searchInput.addEventListener('input', (e) => {
    state.searchQuery = e.target.value.toLowerCase().trim();
    el.clearSearch.classList.toggle('hidden', state.searchQuery.length === 0);
    // Wait until typing pauses so large result lists aren't filtered on every keystroke
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => applyFiltersAndRender(), SEARCH_DELAY_MS);
  });

  el.clearSearch.addEventListener('click', () => {
    el.searchInput.value = '';
    state.searchQuery = '';
    el.clearSearch.classList.add('hidden');
    applyFiltersAndRender();
  });

  el.categoryFilter.addEventListener('change', (e) => {
    state.categoryFilter = e.target.value;
    applyFiltersAndRender();
  });

  el.riskFilter.addEventListener('change', (e) => {
    state.riskFilter = e.target.value;
    applyFiltersAndRender();
  });

  // Stat Card Clicks
  document.querySelectorAll('.stat-card[data-category]').forEach((card) => {
    card.addEventListener('click', () => filterByCategory(card.getAttribute('data-category')));
  });

  // Master Checkbox
  el.masterCheckbox.addEventListener('change', (e) => {
    const checkAll = e.target.checked;
    state.filteredItems.forEach((item) => {
      if (checkAll && !item.excluded_by) {
        state.selectedIds.add(item.id);
      } else {
        state.selectedIds.delete(item.id);
      }
    });
    renderTable();
    updateSelectionSummary();
  });

  // Selection Presets
  el.btnSelectAll.addEventListener('click', () => {
    state.filteredItems.forEach((i) => { if (!i.excluded_by) state.selectedIds.add(i.id); });
    renderTable();
    updateSelectionSummary();
  });

  el.btnSelectAllSafe.addEventListener('click', () => {
    state.selectedIds.clear();
    state.items.forEach((i) => {
      // Only what the server itself ticked: Safe and marked Delete (empty folders stay unticked)
      if (i.risk_level === 'Safe' && i.selected && !i.excluded_by && scoreBand(i.score || 0) === 'delete') state.selectedIds.add(i.id);
    });
    renderTable();
    updateSelectionSummary();
    showToast('Selected all safe-to-delete items', 'success');
  });

  el.btnSmartSelect.addEventListener('click', smartSelect);
  el.btnSmartSelectSummary.addEventListener('click', smartSelect);

  el.btnDeselectAll.addEventListener('click', () => {
    state.selectedIds.clear();
    renderTable();
    updateSelectionSummary();
  });

  // Sorting
  document.querySelectorAll('th.sortable').forEach((th) => {
    th.addEventListener('click', () => {
      const field = th.getAttribute('data-sort');
      if (state.sortField === field) {
        state.sortAsc = !state.sortAsc;
      } else {
        state.sortField = field;
        state.sortAsc = true;
      }
      applyFiltersAndRender();
    });
  });

  // Primary Delete Selected Button
  el.btnDeleteItems.addEventListener('click', () => {
    // Only ticked items the current filters show are deleted; hidden ones are left alone
    const selectedItems = visibleSelectedItems();
    if (selectedItems.length === 0) return;

    const totalBytes = selectedItems.reduce((acc, i) => acc + i.size_bytes, 0);
    requestDeleteConfirmation({
      title: 'Confirm Deletion',
      subtitle: `${selectedItems.length} garbage items will be removed`,
      count: selectedItems.length,
      size: formatSize(totalBytes),
      preview: { source: 'scan', paths: selectedItems.map((i) => i.path) },
      onConfirm: async (confirmText) => {
        await executeBatchDelete(selectedItems, confirmText);
      },
    });
  });

  el.btnExportReport.addEventListener('click', exportCsvReport);

  // Delete Mode Toggle
  el.modeRecycle.addEventListener('click', () => setDeleteMode(false));
  el.modePermanent.addEventListener('click', () => setDeleteMode(true));
  el.confirmPermanentAckInput.addEventListener('change', updateExecuteEnabled);
  el.confirmTypedInput.addEventListener('input', updateExecuteEnabled);

  // Hierarchy Explorer Listeners
  el.closeHierarchyModal.addEventListener('click', closeHierarchyModal);
  el.btnCloseHierarchy.addEventListener('click', closeHierarchyModal);

  el.btnUpLevel.addEventListener('click', () => {
    if (state.currentHierarchy && state.currentHierarchy.parent_path) {
      loadHierarchy(state.currentHierarchy.parent_path);
    }
  });

  el.btnDeleteThisFolder.addEventListener('click', () => {
    if (!state.currentHierarchy) return;
    const folderPath = state.currentHierarchy.current_path;
    const folderName = state.currentHierarchy.current_name;
    const folderSize = state.currentHierarchy.total_size_formatted;

    requestDeleteConfirmation({
      title: `Delete Entire Folder Level: "${folderName}"`,
      subtitle: 'All files and subdirectories inside this folder will be deleted',
      count: state.currentHierarchy.total_files + state.currentHierarchy.total_subdirs,
      size: folderSize,
      preview: { source: 'explorer', paths: [folderPath] },
      onConfirm: async (confirmText) => {
        await executeDeleteFolder(folderPath, confirmText);
      },
    });
  });

  el.btnDeleteInitialFile.addEventListener('click', async () => {
    if (!state.initialTargetFilePath) return;
    const filePath = state.initialTargetFilePath;
    const fileName = filePath.split(/[\\/]/).pop();

    requestDeleteConfirmation({
      title: `Delete File Only: "${fileName}"`,
      subtitle: 'Only this specific file will be deleted, keeping the parent folder intact',
      count: 1,
      size: '1 file',
      preview: { source: 'explorer', paths: [filePath] },
      onConfirm: async (confirmText) => {
        await executeExplorerDelete([filePath], confirmText);
        // Refresh hierarchy
        if (state.currentHierarchy) {
          loadHierarchy(state.currentHierarchy.current_path);
        }
      },
    });
  });

  el.btnDeleteHierarchySelected.addEventListener('click', () => {
    const selectedEntries = (state.currentHierarchy?.entries || []).filter((e) =>
      state.hierarchySelectedPaths.has(e.path)
    );
    if (selectedEntries.length === 0) return;

    const totalBytes = selectedEntries.reduce((acc, e) => acc + e.size_bytes, 0);

    requestDeleteConfirmation({
      title: 'Delete Selected Items in Folder',
      subtitle: `${selectedEntries.length} items will be removed`,
      count: selectedEntries.length,
      size: formatSize(totalBytes),
      preview: { source: 'explorer', paths: selectedEntries.map((e) => e.path) },
      onConfirm: async (confirmText) => {
        await executeExplorerDelete(selectedEntries.map((e) => e.path), confirmText);
        if (state.currentHierarchy) {
          loadHierarchy(state.currentHierarchy.current_path);
        }
      },
    });
  });

  el.hierarchyMasterCheck.addEventListener('change', (e) => {
    const checkAll = e.target.checked;
    (state.currentHierarchy?.entries || []).forEach((entry) => {
      if (entry.is_deletable) {
        if (checkAll) {
          state.hierarchySelectedPaths.add(entry.path);
        } else {
          state.hierarchySelectedPaths.delete(entry.path);
        }
      }
    });
    renderHierarchyTable();
    updateHierarchySelectedSummary();
  });

  // Modal Close Handlers
  el.closeConfirmModal.addEventListener('click', closeConfirmModal);
  el.btnCancelDelete.addEventListener('click', closeConfirmModal);
  el.btnExecuteDelete.addEventListener('click', async () => {
    if (state.deleteConfirmCallback) {
      el.btnExecuteDelete.disabled = true;
      el.btnExecuteDelete.innerHTML = '<div class="spinner"></div> Deleting...';
      setDeleting(true);
      try {
        await state.deleteConfirmCallback(el.confirmTypedInput.value.trim());
      } finally {
        setDeleting(false);
        el.btnExecuteDelete.disabled = false;
        closeConfirmModal();
      }
    }
  });

  el.closeAiModal.addEventListener('click', closeAiModal);
  el.btnAiClose.addEventListener('click', closeAiModal);

  // Exclusions
  el.btnOpenExclusions.addEventListener('click', openExclusionsModal);
  el.closeExclusionsModal.addEventListener('click', () => el.exclusionsModal.classList.add('hidden'));
  el.btnCloseExclusions.addEventListener('click', () => el.exclusionsModal.classList.add('hidden'));
  el.btnAddExclusion.addEventListener('click', () => addExclusion(el.exclusionInput.value));
  el.exclusionInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') addExclusion(el.exclusionInput.value);
  });

  // Junk Rules
  el.btnOpenJunkRules.addEventListener('click', openJunkRulesModal);
  el.closeJunkRulesModal.addEventListener('click', () => el.junkRulesModal.classList.add('hidden'));
  el.btnCloseJunkRules.addEventListener('click', () => el.junkRulesModal.classList.add('hidden'));
  el.btnAddJunkRule.addEventListener('click', () => addJunkRule(el.junkRuleInput.value));
  el.junkRuleInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') addJunkRule(el.junkRuleInput.value);
  });

  // New since last scan & Junk by Folder
  el.btnNewOnly.addEventListener('click', () => {
    state.newOnly = !state.newOnly;
    el.btnNewOnly.setAttribute('aria-pressed', String(state.newOnly));
    applyFiltersAndRender();
  });
  el.btnByFolder.addEventListener('click', openByFolderModal);
  el.clearFolderFilter.addEventListener('click', () => setFolderFilter(null));
  el.closeByFolderModal.addEventListener('click', () => el.byFolderModal.classList.add('hidden'));
  el.btnCloseByFolder.addEventListener('click', () => el.byFolderModal.classList.add('hidden'));

  // History
  el.btnOpenHistory.addEventListener('click', openHistoryModal);
  el.closeHistoryModal.addEventListener('click', () => el.historyModal.classList.add('hidden'));
  el.btnCloseHistory.addEventListener('click', () => el.historyModal.classList.add('hidden'));
  el.btnClearHistory.addEventListener('click', clearHistory);
  el.btnForgetLearning.addEventListener('click', forgetLearning);
  el.btnOpenRecycleBin.addEventListener('click', openRecycleBin);

  // Schedule
  el.btnOpenSchedule.addEventListener('click', openScheduleModal);
  el.closeScheduleModal.addEventListener('click', () => el.scheduleModal.classList.add('hidden'));
  el.scheduleFrequency.addEventListener('change', updateScheduleDayVisibility);
  el.btnSaveSchedule.addEventListener('click', saveSchedule);
  el.btnDisableSchedule.addEventListener('click', disableSchedule);
  el.btnLoadReport.addEventListener('click', loadLatestReport);

  // About: version, updates (checked only on click) and diagnostics
  el.btnOpenAbout.addEventListener('click', () => {
    el.updateResult.textContent = '';
    el.aboutModal.classList.remove('hidden');
  });
  el.closeAboutModal.addEventListener('click', () => el.aboutModal.classList.add('hidden'));
  el.btnCloseAbout.addEventListener('click', () => el.aboutModal.classList.add('hidden'));
  el.btnCheckUpdates.addEventListener('click', checkForUpdates);
  el.btnCopyDiagnostics.addEventListener('click', copyDiagnostics);

  // Errors dialog
  el.closeErrorsModal.addEventListener('click', () => el.errorsModal.classList.add('hidden'));
  el.btnCloseErrors.addEventListener('click', () => el.errorsModal.classList.add('hidden'));
  el.btnCopyErrors.addEventListener('click', async () => {
    try {
      await copyText(errorListText(state.shownErrors));
      showToast('Copied the list of paths and reasons', 'success');
    } catch (err) {
      showToast(`Could not copy: ${err.message}`, 'error');
    }
  });

  // Stop a running delete after the current batch
  el.btnCancelClean.addEventListener('click', cancelClean);

  // Largest Files Modal
  el.closeLargestModal.addEventListener('click', () => el.largestModal.classList.add('hidden'));
  el.btnCloseLargest.addEventListener('click', () => el.largestModal.classList.add('hidden'));
  el.largestTabFolders.addEventListener('click', () => renderLargest('folders'));
  el.largestTabFiles.addEventListener('click', () => renderLargest('files'));
}

// Text in the current wording for this system (Recycle Bin or Trash, Explorer or Finder...)
function t(text) {
  return platformText(text, state.platform);
}

// Ask the server which system this is and reword the page to match
async function loadPlatform() {
  try {
    const res = await fetch('/api/system/info');
    const info = await res.json();
    state.platform = info.platform || 'windows';
    state.version = info.version || '';
    if (state.version) {
      el.appVersion.textContent = `v${state.version}`;
      el.aboutVersion.textContent = `Version ${state.version}`;
    }
    if (info.setup_warning) showSetupWarning(info.setup_warning);
  } catch (err) {
    console.error('Failed to load system info:', err);
  }
  if (state.platform !== 'macos') return;
  document.body.classList.add('platform-macos');
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    const text = t(node.nodeValue);
    if (text !== node.nodeValue) node.nodeValue = text;
  }
  document.querySelectorAll('[title], [placeholder]').forEach((node) => {
    ['title', 'placeholder'].forEach((attr) => {
      if (node.hasAttribute(attr)) node.setAttribute(attr, t(node.getAttribute(attr)));
    });
  });
}

// Drive Discovery
async function loadAvailableDrives() {
  try {
    const res = await fetch('/api/system/drives');
    const data = await res.json();
    state.drives = data.drives || [];

    el.driveButtons.innerHTML = '';
    state.drives.forEach((d, idx) => {
      const btn = document.createElement('button');
      btn.className = `drive-pill ${idx === 0 ? 'active' : ''}`;
      btn.dataset.path = d.drive;
      btn.title = `${d.drive} (${d.free_formatted} free)`;
      btn.innerHTML = `<i data-lucide="hard-drive"></i> <strong>${escapeHtml(d.drive)}</strong> <span class="drive-free">${escapeHtml(d.free_formatted)} free</span>`;

      btn.addEventListener('click', () => {
        el.targetPathInput.value = d.drive;
        syncDrivePills();
      });

      el.driveButtons.appendChild(btn);
    });

    if (state.drives.length > 0) {
      el.targetPathInput.value = state.drives[0].drive;
    }
    syncDrivePills();

    lucide.createIcons();
  } catch (err) {
    console.error('Failed to load drives:', err);
  }
}

// Highlight the drive button only when the scan location is that whole drive
function syncDrivePills() {
  const current = el.targetPathInput.value.trim();
  document.querySelectorAll('.drive-pill').forEach((b) => {
    b.classList.toggle('active', samePath(b.dataset.path, current));
  });
}

// Scanning Engine Controller
async function startScan({ junkOnly = false, typeOnly = false } = {}) {
  const targetPath = junkOnly ? '' : el.targetPathInput.value.trim();
  if (!targetPath && !junkOnly) {
    showToast('Please specify a valid path to scan', 'error');
    return;
  }
  if (!junkOnly && (typeOnly || el.optFileTypes.checked) && !state.fileTypes.length) {
    showToast('Pick the file types to look for first', 'error');
    openFileTypesModal();
    return;
  }
  state.typeSearchOnly = typeOnly;
  cancelLiveRender();
  state.scanRun += 1;
  const run = state.scanRun;

  state.items = [];
  state.filteredItems = [];
  state.selectedIds.clear();
  state.isScanning = true;
  resetResultViews();

  el.btnStartScan.classList.add('hidden');
  el.btnScanJunk.classList.add('hidden');
  el.btnStopScan.classList.remove('hidden');
  el.scanProgressContainer.classList.remove('hidden');
  el.scanProgressText.textContent = 'Scanning folders...';
  el.statusText.textContent = junkOnly ? 'Scanning junk locations...'
    : typeOnly ? 'Searching for your file types...' : 'Scanning in progress...';
  document.querySelector('.status-dot').classList.add('scanning');

  resetStatsUI();
  renderTable();

  if (state.eventSource) {
    state.eventSource.close();
  }

  const payload = {
    target_path: targetPath,
    ...currentScanOptions(),
    scan_junk_locations: junkOnly,
    skip_system_dirs: true,
    file_types: state.fileTypes,
    file_type_min_days: state.fileTypeMinDays,
  };
  if (typeOnly) {
    // Only the chosen file types (the server turns every other category off too)
    payload.type_search_only = true;
    payload.include_file_types = true;
  }
  if (junkOnly) {
    // Quick Clean looks only for disposable temp and cache content
    Object.keys(SCAN_OPTION_INPUTS).forEach((key) => { payload[key] = false; });
    payload.include_temp_junk = true;
    payload.include_custom_rules = false;
  } else {
    savePreferences();
  }

  try {
    const res = await fetch('/api/scan/start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });

    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || 'Scan failed to start');
    }

    state.eventSource = new EventSource(`/api/scan/stream?token=${encodeURIComponent(APP_TOKEN)}`);
    let updateThrottleTimer = null;
    let latestStats = null;

    state.eventSource.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);

        if (data.type === 'item_found') {
          markExcluded([data.item]);
          state.items.push(data.item);
          if (data.item.selected && !data.item.excluded_by) {
            state.selectedIds.add(data.item.id);
          }

          latestStats = data.stats;
          if (!updateThrottleTimer) {
            updateThrottleTimer = setTimeout(() => {
              applyFiltersAndRender(false);
              updateMetrics(latestStats);
              updateThrottleTimer = null;
              scheduleLiveRender();
            }, 100);
          }
        } else if (data.type === 'progress') {
          // Sent about every quarter second, also while nothing new is found
          updateScanProgress(data.stats);
        } else if (data.type === 'completed') {
          // Drop any pending throttled update so it cannot overwrite the final stats
          clearTimeout(updateThrottleTimer);
          updateThrottleTimer = null;
          onScanCompleted(data.stats, data.changes, data.error || '');
        }
      } catch (e) {
        console.error('SSE parse error:', e);
      }
    };

    // The stream dropped (EventSource would quietly reconnect and miss messages): ask the
    // server whether the scan is still going before saying it stopped
    state.eventSource.onerror = () => {
      if (state.eventSource) {
        state.eventSource.close();
        state.eventSource = null;
      }
      if (run === state.scanRun && state.isScanning) followScanStatus(run);
    };
  } catch (err) {
    showToast(err.message, 'error');
    stopScan();
  }
}

// Show what the running scan has found so far: items can be inspected, ticked and deleted
// without waiting for the scan to finish. Rows already on screen keep their "Show more" page.
function scheduleLiveRender() {
  if (state.liveRenderTimer) return;
  state.liveRenderTimer = setTimeout(() => {
    state.liveRenderTimer = null;
    if (!state.isScanning) return;
    applyFiltersAndRender(false);
    renderTable();
  }, LIVE_RENDER_MS);
}

function cancelLiveRender() {
  clearTimeout(state.liveRenderTimer);
  state.liveRenderTimer = null;
}

async function followScanStatus(run) {
  const current = () => run === state.scanRun && state.isScanning;
  while (current()) {
    let status;
    try {
      status = await apiJson('/api/scan/status');
    } catch {
      if (current()) onScanCompleted();
      return;
    }
    if (!current()) return;
    const stats = status.stats || status;
    const running = status.is_running ?? (status.status === 'running');
    if (!running) {
      if (status.status === 'idle') onScanCompleted();
      else onScanCompleted(stats, null, status.error || stats.error || '', { streamLost: true });
      return;
    }
    updateScanProgress(stats);
    await new Promise((resolve) => setTimeout(resolve, SCAN_STATUS_POLL_MS));
  }
}

async function stopScan() {
  try {
    await fetch('/api/scan/stop', { method: 'POST' });
  } catch (e) {}
  onScanCompleted();
}

function onScanCompleted(finalStats = null, changes = null, error = '', { streamLost = false } = {}) {
  state.isScanning = false;
  cancelLiveRender();
  if (state.eventSource) {
    state.eventSource.close();
    state.eventSource = null;
  }

  el.btnStartScan.classList.remove('hidden');
  el.btnScanJunk.classList.remove('hidden');
  el.btnStopScan.classList.add('hidden');
  el.scanProgressContainer.classList.add('hidden');
  el.statusText.textContent = error ? 'Scan failed' : 'Scan Finished';
  document.querySelector('.status-dot').classList.remove('scanning');

  if (changes) setNewItems(changes);
  applyFiltersAndRender(true);
  renderSmartSummary();
  if (error) {
    if (finalStats) updateMetrics(finalStats);
    showToast(`The scan stopped because of an error: ${error}. What it found before that is listed.`, 'error');
  } else if (finalStats) {
    updateMetrics(finalStats);
    let note = '';
    if (streamLost) note += ' The live connection dropped, so some results may be missing: scan again to be sure.';
    if (changes && changes.previous_scan_at) {
      const count = state.newPaths.size;
      note = count ? ` ${count.toLocaleString()} new since the last scan.` : ' Nothing new since the last scan.';
    }
    if (state.typeSearchOnly) {
      const n = finalStats.garbage_count;
      showToast(n ? `Found ${plural(n, 'file')} of your chosen types. Tick the ones to remove, then Delete.`
        : 'No files of your chosen types were found here.', n ? 'success' : 'info');
    } else {
      showToast(`Scan finished: ${finalStats.garbage_count} garbage items detected!${note}`, 'success');
    }
  } else {
    showToast(`Scan stopped. Found ${state.items.length} items.`, 'info');
  }
}

// The counters and current step under the scan bar (from item_found, progress or status)
function updateScanProgress(stats) {
  if (!stats) return;
  el.scanMetricsFiles.textContent = `${(Number(stats.files_scanned) || 0).toLocaleString()} files scanned`;
  el.scanMetricsDirs.textContent = `${(Number(stats.dirs_scanned) || 0).toLocaleString()} folders`;
  el.scanMetricsRate.textContent = `${(Number(stats.garbage_count) || 0).toLocaleString()} found (${formatSize(stats.garbage_bytes)})`;
  const label = scanPhaseLabel(stats.phase);
  if (label && state.isScanning) el.scanProgressText.textContent = `${label}...`;
}

function updateMetrics(stats) {
  if (!stats) return;

  updateScanProgress(stats);

  el.statTotalGarbageSize.textContent = formatSize(stats.garbage_bytes);
  el.statTotalGarbageCount.textContent = `${plural(stats.garbage_count, 'item')} ready for review`;

  const counts = stats.category_counts || {};
  const bytes = stats.category_bytes || {};

  const catInstallers = 'Installers, Setup Archives & OS Images';
  el.statInstallersSize.textContent = formatSize(bytes[catInstallers] || 0);
  el.statInstallersCount.textContent = plural(counts[catInstallers], 'file');

  const catJava = 'Old Java & Build Artifacts';
  el.statJavaSize.textContent = formatSize(bytes[catJava] || 0);
  el.statJavaCount.textContent = plural(counts[catJava], 'item');

  const catTemp = 'Temporary & Cache Files';
  el.statTempSize.textContent = formatSize(bytes[catTemp] || 0);
  el.statTempCount.textContent = plural(counts[catTemp], 'file');

  const catDownloads = 'Broken / Incomplete Downloads';
  el.statDownloadsSize.textContent = formatSize(bytes[catDownloads] || 0);
  el.statDownloadsCount.textContent = plural(counts[catDownloads], 'file');

  el.statEmptyFoldersCount.textContent = (counts['Empty Folders'] || 0).toLocaleString();

  const catStale = 'Stale Large Files';
  el.statStaleSize.textContent = formatSize(bytes[catStale] || 0);
  el.statStaleCount.textContent = plural(counts[catStale], 'file');

  const catDuplicates = 'Duplicate Files';
  el.statDuplicatesSize.textContent = formatSize(bytes[catDuplicates] || 0);
  const copies = counts[catDuplicates] || 0;
  el.statDuplicatesCount.textContent = `${copies.toLocaleString()} extra ${copies === 1 ? 'copy' : 'copies'}`;

  const catOldDownloads = 'Old Downloads';
  el.statOldDownloadsSize.textContent = formatSize(bytes[catOldDownloads] || 0);
  el.statOldDownloadsCount.textContent = plural(counts[catOldDownloads], 'file');

  renderSpaceBreakdown(bytes);
}

function renderSpaceBreakdown(categoryBytes) {
  const parts = spaceBreakdown(categoryBytes || {});
  el.spaceBreakdown.classList.toggle('hidden', parts.length === 0);
  if (parts.length === 0) return;

  const total = parts.reduce((acc, p) => acc + p.bytes, 0);
  el.spaceBreakdownTotal.textContent = formatSize(total);
  el.spaceBar.setAttribute('aria-label', parts.map((p) => `${p.label} ${formatSize(p.bytes)}`).join(', '));
  el.spaceBar.innerHTML = parts.map((p) => `
    <div class="space-bar-segment" data-category="${escapeHtml(p.name)}"
         style="flex-grow: ${p.bytes}; flex-basis: 0; background: var(--series-${p.slot});"
         title="${escapeHtml(p.label)}: ${formatSize(p.bytes)} (${p.percent.toFixed(1)}%)"></div>
  `).join('');
  el.spaceLegend.innerHTML = parts.map((p) => `
    <li data-category="${escapeHtml(p.name)}" title="Show only ${escapeHtml(p.label)}">
      <span class="legend-swatch" style="background: var(--series-${p.slot});"></span>
      ${escapeHtml(p.label)} <strong>${formatSize(p.bytes)}</strong> (${p.percent.toFixed(1)}%)
    </li>
  `).join('');

  el.spaceBreakdown.querySelectorAll('[data-category]').forEach((node) => {
    node.addEventListener('click', () => filterByCategory(node.dataset.category));
  });
}

function filterByCategory(category) {
  el.categoryFilter.value = category;
  state.categoryFilter = category;
  applyFiltersAndRender();
}

function resetStatsUI() {
  el.statTotalGarbageSize.textContent = '0.00 MB';
  el.statTotalGarbageCount.textContent = 'Scanning...';
  el.statInstallersSize.textContent = '0.00 MB';
  el.statInstallersCount.textContent = '0 files';
  el.statJavaSize.textContent = '0.00 MB';
  el.statJavaCount.textContent = '0 items';
  el.statTempSize.textContent = '0.00 MB';
  el.statTempCount.textContent = '0 files';
  el.statDownloadsSize.textContent = '0.00 MB';
  el.statDownloadsCount.textContent = '0 files';
  el.statEmptyFoldersCount.textContent = '0';
  el.statStaleSize.textContent = '0.00 MB';
  el.statStaleCount.textContent = '0 files';
  el.statDuplicatesSize.textContent = '0.00 MB';
  el.statDuplicatesCount.textContent = '0 extra copies';
  el.statOldDownloadsSize.textContent = '0.00 MB';
  el.statOldDownloadsCount.textContent = '0 files';
  el.spaceBreakdown.classList.add('hidden');
}

// Filtering & Sorting
function applyFiltersAndRender(renderFull = true) {
  let result = state.items;

  if (state.categoryFilter !== 'ALL') {
    result = result.filter((i) => i.category === state.categoryFilter);
  }

  if (state.riskFilter !== 'ALL') {
    result = result.filter((i) => i.risk_level === state.riskFilter);
  }

  if (state.folderFilter) {
    result = result.filter((i) => parentFolder(i.path).toLowerCase() === state.folderFilter);
  }

  if (state.newOnly) {
    result = result.filter((i) => state.newPaths.has(i.path));
  }

  if (state.searchQuery) {
    const q = state.searchQuery;
    result = result.filter(
      (i) => i.name.toLowerCase().includes(q) || i.path.toLowerCase().includes(q)
    );
  }

  result.sort((a, b) => {
    let valA = a[state.sortField];
    let valB = b[state.sortField];

    if (state.sortField === 'size') {
      valA = a.size_bytes;
      valB = b.size_bytes;
    } else if (state.sortField === 'modified') {
      valA = a.modified_timestamp;
      valB = b.modified_timestamp;
    }

    if (typeof valA === 'string') {
      return state.sortAsc ? valA.localeCompare(valB) : valB.localeCompare(valA);
    }
    return state.sortAsc ? valA - valB : valB - valA;
  });

  state.filteredItems = result;
  updateViewIndicators();
  if (renderFull) {
    state.renderLimit = TABLE_PAGE_SIZE;
    renderTable();
  }
  updateSelectionSummary();
}

// Show the sorted column and its direction, and which category card is filtering the table
function updateViewIndicators() {
  document.querySelectorAll('th.sortable').forEach((th) => {
    if (th.dataset.sort === state.sortField) {
      th.setAttribute('aria-sort', state.sortAsc ? 'ascending' : 'descending');
    } else {
      th.removeAttribute('aria-sort');
    }
  });
  document.querySelectorAll('.stat-card[data-category]').forEach((card) => {
    card.classList.toggle('active', card.dataset.category === state.categoryFilter);
  });
}

// Render Main File Manager Table
function renderTable() {
  if (state.filteredItems.length === 0) {
    el.fileTableBody.innerHTML = `
      <tr class="empty-row">
        <td colspan="11">
          <div class="empty-state">
            <div class="empty-icon-wrap">${state.isScanning ? '<div class="spinner spinner-lg"></div>' : '<i data-lucide="search-x" class="empty-icon"></i>'}</div>
            <h3>${state.isScanning ? 'Scanning in progress...' : state.items.length ? 'No items match your filters' : 'No junk found'}</h3>
            <p>${state.isScanning ? 'Files will appear here as they are discovered.' : state.items.length ? 'Try a different search, category or risk level.' : 'This location looks clean. Try another folder or turn on more categories.'}</p>
          </div>
        </td>
      </tr>
    `;
    lucide.createIcons();
    return;
  }

  const shown = state.filteredItems.slice(0, state.renderLimit);
  const html = shown.map((item) => {
    const excludedBy = item.excluded_by || null;
    const isChecked = state.selectedIds.has(item.id) && !excludedBy;
    // Excluded by its own path (untick to undo) or by another rule (change that under Exclusions)
    const ownRule = excludedBy && normRulePath(excludedBy) === normRulePath(item.path);
    const excludeTitle = !excludedBy
      ? `Exclude: never delete this ${item.is_directory ? 'folder' : 'file'} or list it again (until you remove it under Exclusions)`
      : ownRule ? 'Excluded. Untick to allow deleting it again.'
        : `Excluded by "${excludedBy}". Remove that rule under Exclusions to allow deleting it.`;
    const riskClass = item.risk_level === 'Safe' ? 'risk-safe' : item.risk_level === 'Caution' ? 'risk-caution' : 'risk-review';
    const badge = categoryBadge(item.category);

    let typeIcon = 'file';
    let typeClass = '';
    const lowerName = item.name.toLowerCase();

    if (item.is_directory) {
      typeIcon = 'folder';
      typeClass = 'type-dir';
    } else if (lowerName.endsWith('.apk')) {
      typeIcon = 'smartphone';
      typeClass = 'type-apk';
    } else if (lowerName.endsWith('.iso') || lowerName.endsWith('.img') || lowerName.endsWith('.vhd')) {
      typeIcon = 'disc';
      typeClass = 'type-exe';
    } else if (lowerName.endsWith('.zip') || lowerName.endsWith('.rar') || lowerName.endsWith('.7z')) {
      typeIcon = 'archive';
      typeClass = 'type-tmp';
    } else if (lowerName.endsWith('.exe') || lowerName.endsWith('.msi')) {
      typeIcon = 'box';
      typeClass = 'type-exe';
    } else if (lowerName.endsWith('.class') || lowerName.endsWith('.jar')) {
      typeIcon = 'coffee';
      typeClass = 'type-class';
    } else if (lowerName.endsWith('.tmp') || lowerName.endsWith('.log') || lowerName.endsWith('.dmp')) {
      typeIcon = 'trash-2';
      typeClass = 'type-tmp';
    }

    return `
      <tr class="${isChecked ? 'row-selected' : ''}${excludedBy ? ' row-excluded' : ''}" data-id="${escapeHtml(item.id)}">
        <td>
          <input type="checkbox" class="row-checkbox" data-id="${escapeHtml(item.id)}" ${isChecked ? 'checked' : ''}
            ${excludedBy ? 'disabled title="Excluded items can\'t be deleted"' : ''} aria-label="Select ${escapeHtml(item.name)}" />
        </td>
        <td>
          <i data-lucide="${typeIcon}" class="file-type-icon ${typeClass}"></i>
        </td>
        <td>
          <div class="file-name-cell clickable-file-cell" title="Click to view parent folder and hierarchy" data-path="${escapeHtml(item.path)}">
            <span class="file-name-text">${escapeHtml(item.name)}</span>${state.newPaths.has(item.path) ? '<span class="new-badge" title="Not found by the previous scan of this folder">New</span>' : ''}
          </div>
        </td>
        <td class="exclude-cell">
          <input type="checkbox" class="exclude-checkbox" data-id="${escapeHtml(item.id)}" ${excludedBy ? 'checked' : ''}
            ${excludedBy && !ownRule ? 'disabled' : ''} title="${escapeHtml(excludeTitle)}" aria-label="Exclude ${escapeHtml(item.name)}" />
        </td>
        <td>
          <span class="category-badge" data-slot="${badge.slot}" title="${escapeHtml(item.category)}">${escapeHtml(badge.label)}</span>
        </td>
        <td class="text-right">
          <span class="file-size-cell">${escapeHtml(item.size_formatted)}</span>
        </td>
        <td>
          <span class="file-date-cell">${escapeHtml(item.modified_date)}</span>
        </td>
        <td>
          <span class="risk-badge ${riskClass}">${escapeHtml(item.risk_level)}</span>
        </td>
        <td>
          <button type="button" class="score-cell" data-id="${escapeHtml(item.id)}" aria-expanded="${state.expandedScoreId === item.id}"
                  title="${escapeHtml((item.score_reasons || []).join('\n'))}">
            ${item.score ? `<span class="score-pill score-${scoreBand(item.score)}">${escapeHtml(item.recommendation || '')}</span>
            <span class="score-num">${Number(item.score)}</span>` : '<span class="score-num">&mdash;</span>'}
          </button>
        </td>
        <td>
          <div class="file-path-cell" title="${escapeHtml(item.path)}">${escapeHtml(item.path)}</div>
        </td>
        <td>
          <div class="row-actions">
            <button class="action-icon-btn btn-inspect-hierarchy" title="Explore parent folder hierarchy" data-path="${escapeHtml(item.path)}">
              <i data-lucide="folder-tree"></i>
            </button>
            <button class="action-icon-btn btn-open-folder" title="${t('Open containing folder in Windows Explorer')}" data-path="${escapeHtml(item.path)}">
              <i data-lucide="folder"></i>
            </button>
            <button class="action-icon-btn ai-btn btn-inspect-ai" title="AI Inspection & Advice" data-path="${escapeHtml(item.path)}">
              <i data-lucide="bot"></i>
            </button>
          </div>
        </td>
      </tr>${state.expandedScoreId === item.id ? `
      <tr class="score-reasons-row">
        <td colspan="11">
          <div class="score-reasons">
            <strong>Why ${Number(item.score) || 0}/100 (${escapeHtml(item.recommendation || '')})</strong>
            <ul>${(item.score_reasons || []).map((r) => `<li>${escapeHtml(r)}</li>`).join('')}</ul>
          </div>
        </td>
      </tr>` : ''}
    `;
  }).join('');

  const remaining = state.filteredItems.length - shown.length;
  const moreRow = remaining > 0 ? `
      <tr class="more-row">
        <td colspan="11">
          <button type="button" class="btn btn-secondary btn-show-more">
            Show ${Math.min(remaining, TABLE_PAGE_SIZE).toLocaleString()} more (${remaining.toLocaleString()} not shown yet)
          </button>
        </td>
      </tr>` : '';

  el.fileTableBody.innerHTML = html + moreRow;
  lucide.createIcons({ root: el.fileTableBody });
}

// Table events are handled once on the table body (rows are re-rendered often)
function bindTableEvents() {
  el.fileTableBody.addEventListener('change', (e) => {
    const excludeBox = e.target.closest('.exclude-checkbox');
    if (excludeBox) {
      const item = state.items.find((i) => i.id === excludeBox.dataset.id);
      if (item) setItemExcluded(item, excludeBox.checked);
      return;
    }
    const chk = e.target.closest('.row-checkbox');
    if (!chk) return;
    const id = chk.dataset.id;
    if (chk.checked) {
      state.selectedIds.add(id);
    } else {
      state.selectedIds.delete(id);
    }
    chk.closest('tr').classList.toggle('row-selected', chk.checked);
    updateSelectionSummary();
  });

  el.fileTableBody.addEventListener('click', async (e) => {
    if (e.target.closest('.btn-show-more')) {
      state.renderLimit += TABLE_PAGE_SIZE;
      renderTable();
      return;
    }

    const scoreCell = e.target.closest('.score-cell');
    if (scoreCell) {
      state.expandedScoreId = state.expandedScoreId === scoreCell.dataset.id ? null : scoreCell.dataset.id;
      renderTable();
      return;
    }

    const hierarchy = e.target.closest('.clickable-file-cell, .btn-inspect-hierarchy');
    if (hierarchy) {
      openHierarchyModal(hierarchy.dataset.path);
      return;
    }

    const openFolder = e.target.closest('.btn-open-folder');
    if (openFolder) {
      e.stopPropagation();
      try {
        await fetch('/api/system/open-explorer', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ path: openFolder.dataset.path }),
        });
      } catch (err) {
        showToast('Could not open folder in Explorer', 'error');
      }
      return;
    }

    const ai = e.target.closest('.btn-inspect-ai');
    if (ai) {
      e.stopPropagation();
      openAiInspector(ai.dataset.path);
    }
  });
}

// Tick only the items JunkZero is confident about (marked Delete)
function smartSelect() {
  state.selectedIds.clear();
  const picked = state.items.filter((i) => !i.excluded_by && scoreBand(i.score || 0) === 'delete');
  picked.forEach((i) => state.selectedIds.add(i.id));
  renderTable();
  updateSelectionSummary();
  const bytes = picked.reduce((acc, i) => acc + i.size_bytes, 0);
  showToast(picked.length
    ? `Selected ${picked.length.toLocaleString()} items marked Delete (${formatSize(bytes)})`
    : 'No items are marked Delete; review the rest before deleting', picked.length ? 'success' : 'info');
}

function renderSmartSummary() {
  const summary = smartSummary(state.items.filter((i) => !i.excluded_by), formatSize);
  el.smartSummary.classList.toggle('hidden', summary.lines.length === 0);
  el.btnSmartSelectSummary.classList.toggle('hidden', summary.deleteCount === 0);
  el.smartSummaryList.innerHTML = summary.lines.map((l) => `
    <li><span class="score-dot score-${l.band}"></span>${escapeHtml(l.text)}</li>
  `).join('');
}

// Ticked items that the current filters show (the ones "Delete Selected" removes)
function visibleSelectedItems() {
  return state.filteredItems.filter((i) => state.selectedIds.has(i.id) && !i.excluded_by);
}

// Excluded items are never ticked, whichever button tried to tick them
function dropExcludedFromSelection() {
  state.items.forEach((i) => { if (i.excluded_by) state.selectedIds.delete(i.id); });
}

// Update Footer Selection Summary
function updateSelectionSummary() {
  dropExcludedFromSelection();
  const selectedItems = visibleSelectedItems();
  const count = selectedItems.length;
  const totalBytes = selectedItems.reduce((acc, i) => acc + i.size_bytes, 0);

  el.selectedCount.textContent = `${count.toLocaleString()} items`;
  el.selectedSize.textContent = formatSize(totalBytes);

  const hiddenCount = state.items.reduce((n, i) => n + (state.selectedIds.has(i.id) ? 1 : 0), 0) - count;
  el.hiddenSelectedNote.textContent = hiddenCount > 0
    ? `(${hiddenCount.toLocaleString()} more ticked but hidden by filters, not deleted)`
    : '';
  el.hiddenSelectedNote.classList.toggle('hidden', hiddenCount <= 0);

  el.btnDeleteItems.disabled = count === 0;

  if (state.filteredItems.length === 0) {
    el.masterCheckbox.checked = false;
    el.masterCheckbox.indeterminate = false;
  } else {
    const selectable = state.filteredItems.filter((i) => !i.excluded_by);
    const allFilteredChecked = selectable.length > 0 && selectable.every((i) => state.selectedIds.has(i.id));
    const someFilteredChecked = selectable.some((i) => state.selectedIds.has(i.id));

    el.masterCheckbox.checked = allFilteredChecked;
    el.masterCheckbox.indeterminate = someFilteredChecked && !allFilteredChecked;
  }
}

// ==========================================
// Folder Hierarchy & Multi-Level Explorer
// ==========================================

async function openHierarchyModal(targetPath) {
  state.initialTargetFilePath = targetPath;
  state.hierarchySelectedPaths.clear();
  el.hierarchyModal.classList.remove('hidden');
  await loadHierarchy(targetPath);
}

async function loadHierarchy(path) {
  try {
    el.hierarchyTableBody.innerHTML = `
      <tr>
        <td colspan="7" style="text-align: center; padding: 40px;">
          <div class="spinner" style="margin: 0 auto 10px;"></div>
          <span>Inspecting directory hierarchy...</span>
        </td>
      </tr>
    `;

    const res = await fetch('/api/filesystem/folder-hierarchy', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path }),
    });

    if (!res.ok) {
      throw new Error('Could not load folder hierarchy');
    }

    const data = await res.json();
    state.currentHierarchy = data;
    state.hierarchySelectedPaths.clear();

    // Render Breadcrumbs
    renderHierarchyBreadcrumbs(data.breadcrumbs);

    // Up to Parent Button
    el.btnUpLevel.disabled = !data.parent_path;

    // Folder Meta
    el.hierarchyFolderName.textContent = data.current_name;
    el.hierarchyFolderMeta.textContent = `${data.total_files} files, ${data.total_subdirs} folders • Total: ${data.total_size_formatted}`;

    // Delete Entire Folder Level Button
    el.btnDeleteThisFolder.disabled = !data.is_current_deletable;
    if (!data.is_current_deletable) {
      el.btnDeleteThisFolder.title = 'Cannot delete drive root or protected system directory';
    } else {
      el.btnDeleteThisFolder.title = `Delete folder "${data.current_name}" and everything in it`;
    }

    // Delete Initial File Button
    if (state.initialTargetFilePath) {
      el.btnDeleteInitialFile.classList.remove('hidden');
      const targetBase = state.initialTargetFilePath.split(/[\\/]/).pop();
      el.btnDeleteInitialFile.innerHTML = `<i data-lucide="file-minus"></i> Delete File Only ("${escapeHtml(targetBase)}")`;
    } else {
      el.btnDeleteInitialFile.classList.add('hidden');
    }

    // Render Entries
    renderHierarchyTable();
    updateHierarchySelectedSummary();
    lucide.createIcons();
  } catch (err) {
    showToast(`Error: ${err.message}`, 'error');
  }
}

function renderHierarchyBreadcrumbs(breadcrumbs) {
  el.hierarchyBreadcrumbs.innerHTML = breadcrumbs.map((b, idx) => {
    const isLast = b.is_current;
    return `
      <button class="breadcrumb-pill ${isLast ? 'active' : ''}" data-path="${escapeHtml(b.path)}" title="${escapeHtml(b.path)}">
        ${escapeHtml(b.name)}
      </button>
      ${!isLast ? '<i data-lucide="chevron-right" class="breadcrumb-sep"></i>' : ''}
    `;
  }).join('');

  document.querySelectorAll('.breadcrumb-pill').forEach((pill) => {
    pill.addEventListener('click', () => {
      const path = pill.dataset.path;
      loadHierarchy(path);
    });
  });
}

function renderHierarchyTable() {
  if (!state.currentHierarchy) return;
  const entries = state.currentHierarchy.entries;

  if (entries.length === 0) {
    el.hierarchyTableBody.innerHTML = `
      <tr>
        <td colspan="7" style="text-align: center; padding: 40px; color: var(--text-muted);">
          Folder is empty.
        </td>
      </tr>
    `;
    return;
  }

  const html = entries.map((entry) => {
    const isChecked = state.hierarchySelectedPaths.has(entry.path);
    const isTarget = entry.is_target_file;
    const isDir = entry.is_dir;

    let statusBadge = '';
    if (isTarget) {
      statusBadge = '<span class="risk-badge risk-caution"><i data-lucide="target"></i> Initial Target</span>';
    } else if (entry.is_garbage) {
      statusBadge = `<span class="risk-badge ${entry.risk_level === 'Safe' ? 'risk-safe' : 'risk-review'}">${escapeHtml(entry.garbage_category || 'Garbage')}</span>`;
    } else {
      statusBadge = '<span class="category-badge">Standard File</span>';
    }

    return `
      <tr class="${isTarget ? 'target-row' : ''} ${isChecked ? 'row-selected' : ''}">
        <td>
          <input type="checkbox" class="hierarchy-row-checkbox" data-path="${escapeHtml(entry.path)}" ${isChecked ? 'checked' : ''} ${!entry.is_deletable ? 'disabled' : ''} />
        </td>
        <td>
          <i data-lucide="${isDir ? 'folder' : 'file'}" class="file-type-icon ${isDir ? 'type-dir' : ''}"></i>
        </td>
        <td>
          <div class="file-name-cell" style="cursor: ${isDir ? 'pointer' : 'default'};" data-path="${escapeHtml(entry.path)}" data-isdir="${isDir}">
            <span class="file-name-text" style="${isDir ? 'font-weight: 600; color: var(--color-cyan);' : ''}">${escapeHtml(entry.name)}</span>
          </div>
        </td>
        <td><span class="file-size-cell">${escapeHtml(entry.size_formatted)}</span></td>
        <td><span class="file-date-cell">${escapeHtml(entry.modified_date)}</span></td>
        <td>${statusBadge}</td>
        <td>
          <div class="row-actions">
            ${entry.is_deletable ? `
              <button class="action-icon-btn btn-delete-single-entry text-danger" data-path="${escapeHtml(entry.path)}" data-name="${escapeHtml(entry.name)}" data-size="${escapeHtml(entry.size_formatted)}" data-isdir="${isDir}" title="Permanently delete this ${isDir ? 'folder' : 'file'}">
                <i data-lucide="trash-2"></i>
              </button>
            ` : ''}
          </div>
        </td>
      </tr>
    `;
  }).join('');

  el.hierarchyTableBody.innerHTML = html;
  lucide.createIcons();

  // Checkbox handlers
  document.querySelectorAll('.hierarchy-row-checkbox').forEach((chk) => {
    chk.addEventListener('change', (e) => {
      const path = e.target.dataset.path;
      if (e.target.checked) {
        state.hierarchySelectedPaths.add(path);
      } else {
        state.hierarchySelectedPaths.delete(path);
      }
      e.target.closest('tr').classList.toggle('row-selected', e.target.checked);
      updateHierarchySelectedSummary();
    });
  });

  // Clicking subfolder navigates into it
  document.querySelectorAll('.file-name-cell[data-isdir="true"]').forEach((cell) => {
    cell.addEventListener('click', () => {
      const path = cell.dataset.path;
      loadHierarchy(path);
    });
  });

  // Single Item Delete Button
  document.querySelectorAll('.btn-delete-single-entry').forEach((btn) => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      const path = btn.dataset.path;
      const name = btn.dataset.name;
      const size = btn.dataset.size;
      const isDir = btn.dataset.isdir === 'true';

      requestDeleteConfirmation({
        title: `Delete ${isDir ? 'Folder' : 'File'}: "${name}"`,
        subtitle: `This ${isDir ? 'folder and all its contents' : 'file'} will be removed`,
        count: 1,
        size: size,
        preview: { source: 'explorer', paths: [path] },
        onConfirm: async (confirmText) => {
          await executeExplorerDelete([path], confirmText);
          if (state.currentHierarchy) {
            loadHierarchy(state.currentHierarchy.current_path);
          }
        },
      });
    });
  });
}

function updateHierarchySelectedSummary() {
  const count = state.hierarchySelectedPaths.size;
  el.hierarchySelectedSummary.textContent = `${count} item${count === 1 ? '' : 's'} selected in this folder`;
  el.btnDeleteHierarchySelected.disabled = count === 0;

  const entries = state.currentHierarchy?.entries || [];
  const deletableEntries = entries.filter((e) => e.is_deletable);
  const allChecked = deletableEntries.length > 0 && deletableEntries.every((e) => state.hierarchySelectedPaths.has(e.path));
  const someChecked = deletableEntries.some((e) => state.hierarchySelectedPaths.has(e.path));

  el.hierarchyMasterCheck.checked = allChecked;
  el.hierarchyMasterCheck.indeterminate = someChecked && !allChecked;
}

function closeHierarchyModal() {
  el.hierarchyModal.classList.add('hidden');
  state.currentHierarchy = null;
  state.initialTargetFilePath = null;
  state.hierarchySelectedPaths.clear();
}

// Delete Entire Folder Level Handler
async function executeDeleteFolder(folderPath, confirmText = '') {
  try {
    const result = await apiJson('/api/filesystem/delete-folder', {
      method: 'POST',
      body: JSON.stringify({ path: folderPath, permanent: state.permanentDelete, confirm_text: confirmText }),
    });
    if (result.deleted_count > 0) {
      const verb = result.mode === 'permanent' ? 'Permanently deleted'
        : (Object.keys(result.held || {}).length ? 'Set aside for 7 days' : t('Moved to Recycle Bin'));
      showToast(`${verb}: ${folderPath}`, 'success');

      // Purge any items from main scan that were inside this deleted folder
      state.items = state.items.filter((i) => !pathIsWithin(i.path, folderPath));
      applyFiltersAndRender(true);

      // Navigate to parent folder or close if at root
      if (state.currentHierarchy && state.currentHierarchy.parent_path) {
        await loadHierarchy(state.currentHierarchy.parent_path);
      } else {
        closeHierarchyModal();
      }
    } else if (result.failed_count > 0) {
      showToast(`Could not delete folder: ${result.errors[0]?.error || 'Locked or permission denied'}`, 'error',
        { details: result.errors });
    }
  } catch (err) {
    showToast(`Folder deletion failed: ${err.message}`, 'error');
  }
}

// Batch Deletion of scan results (Recycle Bin or permanent, per the current delete mode)
async function executeBatchDelete(items, confirmText = '') {
  const job = startCleanProgress();
  try {
    const result = await apiJson('/api/clean', {
      method: 'POST',
      body: JSON.stringify({
        items: items.map((i) => ({ path: i.path })),
        permanent: state.permanentDelete,
        confirm_text: confirmText,
      }),
    });
    showDeleteOutcome(result);

    // After Cancel, items not reached yet were left alone. Without their paths we can't tell
    // which rows are gone, so the table stays as it is until the next scan.
    const skippedKnown = Array.isArray(result.skipped);
    if (result.deleted_count > 0 && (skippedKnown || !(result.skipped_count > 0))) {
      const failed = new Set((result.errors || []).map((e) => e.path));
      (skippedKnown ? result.skipped : []).forEach((p) => failed.add(typeof p === 'string' ? p : p?.path));
      const deletedPaths = new Set(items.map((i) => i.path).filter((p) => !failed.has(p)));
      state.items = state.items.filter((i) => !deletedPaths.has(i.path));
      items.forEach((i) => { if (deletedPaths.has(i.path)) state.selectedIds.delete(i.id); });

      applyFiltersAndRender(true);
      updateSelectionSummary();
      renderSmartSummary();
    }
  } catch (err) {
    showToast(`Deletion failed: ${err.message}`, 'error');
  } finally {
    stopCleanProgress(job);
  }
}

// While /api/clean runs: "Deleting 1,200 of 3,000", a progress bar and Cancel. An older
// server without /api/clean/progress just shows "Deleting..." until it is done.
function startCleanProgress() {
  const job = { active: true };
  state.cleanJob = job;
  el.deleteProgress.classList.remove('hidden');
  el.deleteProgressText.textContent = 'Deleting...';
  el.deleteProgressBar.classList.add('indeterminate');
  el.deleteProgressBar.style.width = '';
  el.btnCancelClean.classList.add('hidden');
  el.btnCancelClean.disabled = false;
  el.btnCancelClean.textContent = 'Cancel';
  const poll = async () => {
    if (!job.active) return;
    try {
      const res = await fetch('/api/clean/progress');
      if (res.status === 404) return; // this server can't report progress
      if (res.ok) {
        const progress = await res.json();
        if (job.active) renderCleanProgress(progress);
      }
    } catch { /* try again next tick */ }
    if (job.active) setTimeout(poll, CLEAN_PROGRESS_MS);
  };
  setTimeout(poll, CLEAN_PROGRESS_MS);
  return job;
}

function renderCleanProgress(progress) {
  el.deleteProgressText.textContent = deleteProgressText(progress);
  const percent = deleteProgressPercent(progress);
  el.deleteProgressBar.classList.toggle('indeterminate', percent === null);
  el.deleteProgressBar.style.width = percent === null ? '' : `${percent}%`;
  if (progress.running) el.btnCancelClean.classList.remove('hidden');
}

function stopCleanProgress(job) {
  job.active = false;
  if (state.cleanJob === job) state.cleanJob = null;
  el.deleteProgress.classList.add('hidden');
}

async function cancelClean() {
  el.btnCancelClean.disabled = true;
  el.btnCancelClean.textContent = 'Stopping after this batch...';
  try {
    await apiJson('/api/clean/cancel', { method: 'POST' });
  } catch (err) {
    el.btnCancelClean.disabled = false;
    el.btnCancelClean.textContent = 'Cancel';
    showToast(`Could not cancel: ${err.message}`, 'error');
  }
}

// While anything is being deleted, closing or reloading the window asks first
function setDeleting(deleting) {
  state.isDeleting = deleting;
  el.btnCancelDelete.disabled = deleting;
  el.closeConfirmModal.disabled = deleting;
  window.onbeforeunload = deleting ? (e) => {
    e.preventDefault();
    e.returnValue = '';
    return '';
  } : null;
}

// Files and folders picked in Folder Explorer
async function executeExplorerDelete(paths, confirmText = '') {
  try {
    const result = await apiJson('/api/filesystem/delete-paths', {
      method: 'POST',
      body: JSON.stringify({ paths, permanent: state.permanentDelete, confirm_text: confirmText }),
    });
    showDeleteOutcome(result);
    if (result.deleted_count > 0) {
      const failed = new Set((result.errors || []).map((e) => e.path));
      const gone = paths.filter((p) => !failed.has(p));
      state.items = state.items.filter((i) => !gone.some((p) => pathIsWithin(i.path, p)));
      applyFiltersAndRender(true);
      updateSelectionSummary();
    }
  } catch (err) {
    showToast(`Deletion failed: ${err.message}`, 'error');
  }
}

function showDeleteOutcome(result) {
  const held = Object.keys(result.held || {}).length;
  if (result.deleted_count > 0) {
    if (result.mode === 'permanent') {
      showToast(`Permanently deleted ${result.deleted_count} item(s) (freed ${result.freed_formatted})!`, 'success');
    } else {
      const binned = result.deleted_count - held;
      if (binned > 0) {
        showToast(t(`Moved ${binned} item(s) to the Recycle Bin. Empty it to free the space.`), 'success');
      }
      if (held > 0) {
        showToast(t(`${held} item(s) on a drive without a Recycle Bin were set aside in a hidden JunkZero folder on that drive for 7 days. Restore them from Cleanup History.`), 'info');
      }
    }
  }
  if (result.skipped_count > 0) {
    const how = Array.isArray(result.skipped) ? '' : ' Scan again to refresh the list.';
    showToast(`Cancelled. ${plural(result.skipped_count, 'item')} ${result.skipped_count === 1 ? 'was' : 'were'} not deleted and ${result.skipped_count === 1 ? 'is' : 'are'} still in place.${how}`, 'info');
  }
  if (result.failed_count > 0) {
    const errors = result.errors || [];
    const reason = errors.length === 1 ? `: ${errors[0].error}` : '';
    showToast(`${plural(result.failed_count, 'item')} couldn't be removed${reason}`, 'error', { details: errors });
  }
}

// Delete Mode
function setDeleteMode(permanent) {
  state.permanentDelete = permanent;
  el.modeRecycle.classList.toggle('active', !permanent);
  el.modePermanent.classList.toggle('active', permanent);
  el.modeRecycle.setAttribute('aria-pressed', String(!permanent));
  el.modePermanent.setAttribute('aria-pressed', String(permanent));
  el.btnDeleteItems.innerHTML = permanent
    ? '<i data-lucide="trash-2"></i> Delete Selected Permanently'
    : t('<i data-lucide="trash-2"></i> Move Selected to Recycle Bin');
  lucide.createIcons();
}

// Universal Deletion Confirmation Modal
function requestDeleteConfirmation({ title, subtitle, count, size, preview, onConfirm }) {
  const permanent = state.permanentDelete;
  el.confirmModalTitle.textContent = title;
  el.confirmModalSubtitle.textContent = subtitle;
  el.confirmCount.textContent = count.toLocaleString();
  el.confirmSize.textContent = size;

  el.confirmModalIcon.className = permanent ? 'modal-icon danger' : 'modal-icon warning';
  el.confirmWarningBanner.classList.toggle('danger-banner', permanent);
  el.confirmMode.classList.toggle('text-danger', permanent);

  if (permanent) {
    el.confirmMode.textContent = t('Permanent (No Recycle Bin)');
    el.confirmWarningText.textContent = 'Selected files/folders will be PERMANENTLY erased from disk immediately. This cannot be undone.';
    el.btnExecuteDelete.innerHTML = '<i data-lucide="trash-2"></i> Permanently Delete Now';
  } else {
    el.confirmMode.textContent = t('Recycle Bin');
    el.confirmWarningText.textContent = t('Selected files/folders will be moved to the Recycle Bin. You can restore them from there until it is emptied.');
    el.btnExecuteDelete.innerHTML = t('<i data-lucide="recycle"></i> Move to Recycle Bin');
  }

  // Permanent mode needs an explicit second confirmation before the button unlocks
  el.confirmPermanentAckInput.checked = false;
  el.confirmPermanentAck.classList.toggle('hidden', !permanent);

  // What will be deleted is checked first; large deletes and personal files need DELETE typed
  el.confirmChecks.innerHTML = '';
  el.confirmChecks.classList.add('hidden');
  el.confirmTyped.classList.add('hidden');
  el.confirmTypedInput.value = '';
  state.confirmNeedsTyped = false;
  state.confirmPreviewPending = Boolean(preview);
  updateExecuteEnabled();

  state.deleteConfirmCallback = onConfirm;

  el.confirmModal.classList.remove('hidden');
  lucide.createIcons();
  if (preview) loadDeletePreview(preview);
}

function updateExecuteEnabled() {
  const ackOk = !state.permanentDelete || el.confirmPermanentAckInput.checked;
  const typedOk = !state.confirmNeedsTyped || el.confirmTypedInput.value.trim().toUpperCase() === 'DELETE';
  el.btnExecuteDelete.disabled = state.confirmPreviewPending || !ackOk || !typedOk;
}

async function loadDeletePreview(preview) {
  const request = {};
  state.confirmPreviewRequest = request;
  let lines;
  try {
    const summary = await apiJson('/api/delete-preview', {
      method: 'POST',
      body: JSON.stringify({ ...preview, permanent: state.permanentDelete }),
    });
    if (state.confirmPreviewRequest !== request) return;
    if (preview.source === 'explorer') {
      el.confirmCount.textContent = summary.count.toLocaleString();
      el.confirmSize.textContent = summary.bytes_formatted;
    }
    state.confirmNeedsTyped = summary.needs_typed_confirm;
    lines = deletePreviewLines(summary, t('Recycle Bin'));
  } catch (err) {
    if (state.confirmPreviewRequest !== request) return;
    // Couldn't check: be safe and ask for DELETE
    state.confirmNeedsTyped = true;
    lines = [{ level: 'warning', text: `Could not check what will be deleted (${err.message})` }];
  }
  el.confirmChecks.innerHTML = lines.map((l) => `<li class="check-${l.level}">${escapeHtml(l.text)}</li>`).join('');
  el.confirmChecks.classList.toggle('hidden', lines.length === 0);
  el.confirmTyped.classList.toggle('hidden', !state.confirmNeedsTyped);
  state.confirmPreviewPending = false;
  updateExecuteEnabled();
  if (state.confirmNeedsTyped) el.confirmTypedInput.focus();
}

function closeConfirmModal() {
  if (state.isDeleting) return; // it closes by itself when the delete is done
  el.confirmModal.classList.add('hidden');
  state.deleteConfirmCallback = null;
  state.confirmPreviewRequest = null;
}

// AI Inspector
async function openAiInspector(path) {
  el.aiModal.classList.remove('hidden');
  el.aiModalBody.innerHTML = `
    <div class="ai-loading">
      <div class="spinner"></div>
      <p>Analyzing file structure and safety context...</p>
    </div>
  `;

  try {
    const res = await fetch('/api/ai/analyze', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path }),
    });

    const data = await res.json();
    const verdictClass = data.safety_verdict === 'Safe to Delete' ? 'risk-safe' : 'risk-review';

    el.aiModalBody.innerHTML = `
      <div class="ai-detail-row">
        <span class="ai-detail-label">Target File</span>
        <strong class="ai-detail-value">${escapeHtml(data.file_name)}</strong>
      </div>

      <div class="ai-detail-row">
        <span class="ai-detail-label">File Type & Origin</span>
        <span class="ai-detail-value">${escapeHtml(data.detected_type)} • <em>${escapeHtml(data.origin_application)}</em></span>
      </div>

      <div class="ai-verdict-box">
        <div style="display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px;">
          <span class="ai-detail-label">Safety Verdict</span>
          <div>
            <span class="risk-badge ${verdictClass}">${escapeHtml(data.safety_verdict)}</span>
            ${data.ai_powered ? '<span class="ai-powered-tag"><i data-lucide="sparkles"></i> Gemini AI</span>' : ''}
          </div>
        </div>
        <p style="font-size: 13px; color: var(--text-primary); margin-bottom: 8px;">${escapeHtml(data.explanation)}</p>
        <p style="font-size: 12px; color: var(--color-primary); font-weight: 500;"><strong>Recommendation:</strong> ${escapeHtml(data.recommendation)}</p>
      </div>

      <div class="ai-detail-row">
        <span class="ai-detail-label">Full Path</span>
        <span class="file-path-cell path-wrap">${escapeHtml(data.file_path)}</span>
      </div>
    `;
    lucide.createIcons();
  } catch (err) {
    el.aiModalBody.innerHTML = `<p style="color: var(--color-danger);">Failed to inspect file: ${escapeHtml(err.message)}</p>`;
  }
}

function closeAiModal() {
  el.aiModal.classList.add('hidden');
}

// Export CSV Report (the rows currently shown, after search and filters)
function exportCsvReport() {
  const items = state.filteredItems.length ? state.filteredItems : state.items;
  if (items.length === 0) {
    showToast('No scanned items to export', 'error');
    return;
  }

  const url = URL.createObjectURL(new Blob([buildCsv(items)], { type: 'text/csv;charset=utf-8' }));
  const link = document.createElement('a');
  link.href = url;
  link.download = `junkzero_report_${new Date().toISOString().slice(0, 10)}.csv`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  showToast(`Exported ${items.length.toLocaleString()} item(s) as CSV`, 'success');
}

// ==========================================
// Exclusions
// ==========================================

async function apiJson(url, options = {}) {
  const res = await fetch(url, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || `Request failed (${res.status})`);
  return data;
}

async function openExclusionsModal() {
  el.exclusionsModal.classList.remove('hidden');
  el.exclusionInput.value = '';
  try {
    renderExclusions((await apiJson('/api/exclusions')).rules);
  } catch (err) {
    showToast(`Could not load exclusions: ${err.message}`, 'error');
  }
}

function renderExclusions(rules) {
  if (!rules.length) {
    el.exclusionsList.innerHTML = '<li class="list-empty">No exclusions yet</li>';
    return;
  }
  el.exclusionsList.innerHTML = rules.map((rule) => `
    <li>
      <span><span class="rule-kind">${/[*?[]/.test(rule) ? 'Pattern' : 'Path'}</span>${escapeHtml(rule)}</span>
      <button class="action-icon-btn btn-remove-exclusion" data-rule="${escapeHtml(rule)}" title="Remove">
        <i data-lucide="x"></i>
      </button>
    </li>
  `).join('');
  lucide.createIcons();
  el.exclusionsList.querySelectorAll('.btn-remove-exclusion').forEach((btn) => {
    btn.addEventListener('click', async () => {
      try {
        const remaining = rules.filter((r) => r !== btn.dataset.rule);
        const saved = (await apiJson('/api/exclusions', {
          method: 'PUT', body: JSON.stringify({ rules: remaining }),
        })).rules;
        renderExclusions(saved);
        setExclusionRules(saved);
      } catch (err) {
        showToast(`Could not remove exclusion: ${err.message}`, 'error');
      }
    });
  });
}

async function addExclusion(rule) {
  rule = (rule || '').trim();
  if (!rule) return;
  try {
    const data = await apiJson('/api/exclusions/add', { method: 'POST', body: JSON.stringify({ rule }) });
    el.exclusionInput.value = '';
    if (!el.exclusionsModal.classList.contains('hidden')) renderExclusions(data.rules);
    setExclusionRules(data.rules);
  } catch (err) {
    showToast(`Could not add exclusion: ${err.message}`, 'error');
  }
}

// The Exclusions list changed: mark the matching results, untick them, and redraw
function setExclusionRules(rules) {
  state.exclusionRules = Array.isArray(rules) ? rules : [];
  markExcluded(state.items);
  dropExcludedFromSelection();
  applyFiltersAndRender(false);
  renderTable();
  renderSmartSummary();
}

function markExcluded(items) {
  items.forEach((i) => { i.excluded_by = exclusionRuleFor(i.path, state.exclusionRules); });
}

async function loadExclusionRules() {
  try {
    setExclusionRules((await apiJson('/api/exclusions')).rules);
  } catch (err) {
    console.warn('Could not load exclusions:', err);
  }
}

// The Exclude checkbox in a result row: add or remove that item's own exclusion
async function setItemExcluded(item, exclude) {
  const name = item.name || item.path;
  try {
    let rules;
    if (exclude) {
      rules = (await apiJson('/api/exclusions/add', { method: 'POST', body: JSON.stringify({ rule: item.path }) })).rules;
    } else {
      const remaining = state.exclusionRules.filter((r) => normRulePath(r) !== normRulePath(item.path));
      rules = (await apiJson('/api/exclusions', { method: 'PUT', body: JSON.stringify({ rules: remaining }) })).rules;
    }
    setExclusionRules(rules);
    if (!el.exclusionsModal.classList.contains('hidden')) renderExclusions(rules);
    showToast(exclude
      ? `Excluded ${name}: it can't be deleted and won't be listed again until you remove it under Exclusions.`
      : `${name} is no longer excluded.`, 'success');
  } catch (err) {
    showToast(`Could not ${exclude ? 'exclude' : 'un-exclude'} ${name}: ${err.message}`, 'error');
    renderTable();  // put the checkbox back as it was
  }
}

// ==========================================
// Junk Rules (user patterns flagged as junk)
// ==========================================

async function openJunkRulesModal() {
  el.junkRulesModal.classList.remove('hidden');
  el.junkRuleInput.value = '';
  try {
    renderJunkRules((await apiJson('/api/custom-rules')).rules);
  } catch (err) {
    showToast(`Could not load junk rules: ${err.message}`, 'error');
  }
}

function renderJunkRules(rules) {
  if (!rules.length) {
    el.junkRulesList.innerHTML = '<li class="list-empty">No junk rules yet</li>';
    return;
  }
  el.junkRulesList.innerHTML = rules.map((rule) => `
    <li>
      <span><span class="rule-kind">${/[*?[]/.test(rule) ? 'Pattern' : 'Path'}</span>${escapeHtml(rule)}</span>
      <button class="action-icon-btn btn-remove-junk-rule" data-rule="${escapeHtml(rule)}" title="Remove">
        <i data-lucide="x"></i>
      </button>
    </li>
  `).join('');
  lucide.createIcons();
  el.junkRulesList.querySelectorAll('.btn-remove-junk-rule').forEach((btn) => {
    btn.addEventListener('click', async () => {
      try {
        const remaining = rules.filter((r) => r !== btn.dataset.rule);
        renderJunkRules((await apiJson('/api/custom-rules', {
          method: 'PUT', body: JSON.stringify({ rules: remaining }),
        })).rules);
      } catch (err) {
        showToast(`Could not remove junk rule: ${err.message}`, 'error');
      }
    });
  });
}

async function addJunkRule(rule) {
  rule = (rule || '').trim();
  if (!rule) return;
  try {
    const data = await apiJson('/api/custom-rules/add', { method: 'POST', body: JSON.stringify({ rule }) });
    el.junkRuleInput.value = '';
    renderJunkRules(data.rules);
  } catch (err) {
    showToast(`Could not add junk rule: ${err.message}`, 'error');
  }
}

// ==========================================
// New since last scan & Junk by Folder
// ==========================================

function resetResultViews() {
  state.expandedScoreId = null;
  el.smartSummary.classList.add('hidden');
  state.newPaths = new Set();
  state.newOnly = false;
  state.folderFilter = null;
  el.btnNewOnly.classList.add('hidden');
  el.btnNewOnly.setAttribute('aria-pressed', 'false');
  el.folderFilterChip.classList.add('hidden');
}

function setNewItems(changes) {
  state.newPaths = new Set(changes.new_paths || []);
  const count = state.newPaths.size;
  el.newOnlyLabel.textContent = `New only (${count.toLocaleString()})`;
  el.btnNewOnly.classList.toggle('hidden', count === 0);
  if (changes.previous_scan_at) {
    el.btnNewOnly.title = `Show only items not found by the scan on ${new Date(changes.previous_scan_at * 1000).toLocaleString()}`;
  }
}

function setFolderFilter(folder) {
  state.folderFilter = folder ? folder.toLowerCase() : null;
  el.folderFilterChip.classList.toggle('hidden', !folder);
  el.folderFilterText.textContent = folder || '';
  el.folderFilterChip.title = folder || '';
  applyFiltersAndRender();
}

function openByFolderModal() {
  const groups = junkByFolder(state.items);
  el.byFolderModal.classList.remove('hidden');
  el.byFolderSubtitle.textContent = state.items.length
    ? `${state.items.length.toLocaleString()} items in ${new Set(state.items.map((i) => parentFolder(i.path).toLowerCase())).size.toLocaleString()} folders`
    : 'Run a scan to see which folders hold the most junk';
  if (!groups.length) {
    el.byFolderList.innerHTML = '<li class="list-empty">No results yet</li>';
    return;
  }
  const max = groups[0].bytes || 1;
  el.byFolderList.innerHTML = groups.map((g) => `
    <li>
      <div class="largest-main">
        <div class="history-row">
          <strong class="largest-name" title="${escapeHtml(g.folder)}">${escapeHtml(g.folder)}</strong>
          <span>${formatSize(g.bytes)}</span>
        </div>
        <div class="largest-bar"><span style="width: ${Math.max(1, (g.bytes / max) * 100).toFixed(1)}%"></span></div>
        <div class="history-meta">${g.count.toLocaleString()} item${g.count === 1 ? '' : 's'}</div>
      </div>
      <div class="row-actions">
        <button class="action-icon-btn btn-folder-filter" title="Show only this folder's items in the table" data-folder="${escapeHtml(g.folder)}">
          <i data-lucide="filter"></i>
        </button>
        <button class="action-icon-btn btn-folder-open" title="${t('Show in Windows Explorer')}" data-path="${escapeHtml(g.folder)}">
          <i data-lucide="folder"></i>
        </button>
      </div>
    </li>
  `).join('');
  lucide.createIcons();

  el.byFolderList.querySelectorAll('.btn-folder-filter').forEach((btn) => {
    btn.addEventListener('click', () => {
      setFolderFilter(btn.dataset.folder);
      el.byFolderModal.classList.add('hidden');
    });
  });
  el.byFolderList.querySelectorAll('.btn-folder-open').forEach((btn) => {
    btn.addEventListener('click', async () => {
      try {
        await apiJson('/api/system/open-explorer', { method: 'POST', body: JSON.stringify({ path: btn.dataset.path }) });
      } catch (err) {
        showToast('Could not open folder in Explorer', 'error');
      }
    });
  });
}

// ==========================================
// Cleanup History
// ==========================================

async function openHistoryModal() {
  el.historyModal.classList.remove('hidden');
  el.historyList.innerHTML = '<li class="list-empty">Loading...</li>';
  try {
    renderHistory((await apiJson('/api/history')).history);
    renderLearning(await apiJson('/api/learning'));
  } catch (err) {
    showToast(`Could not load history: ${err.message}`, 'error');
  }
}

function renderLearning(summary) {
  const learned = summary.deleted + summary.kept;
  el.learningNote.textContent = learned
    ? `Smart scores have learned from ${summary.deleted.toLocaleString()} item${summary.deleted === 1 ? '' : 's'} you deleted and ${summary.kept.toLocaleString()} you kept.`
    : 'Smart scores learn from what you delete and what you keep between scans.';
  el.btnForgetLearning.disabled = learned === 0;
}

async function forgetLearning() {
  try {
    renderLearning(await apiJson('/api/learning', { method: 'DELETE' }));
    showToast('Learned choices forgotten. The next scan scores items from scratch.', 'success');
  } catch (err) {
    showToast(`Could not reset learning: ${err.message}`, 'error');
  }
}

function renderHistory(history) {
  const totalFreed = history.reduce((acc, h) => acc + (h.freed_bytes || 0), 0);
  el.historySummary.textContent = history.length
    ? `${history.length} cleanup${history.length === 1 ? '' : 's'}, ${formatSize(totalFreed)} removed in total`
    : 'Nothing cleaned yet';

  if (!history.length) {
    el.historyList.innerHTML = '<li class="list-empty">Cleanups you run will be listed here</li>';
    return;
  }
  el.historyList.innerHTML = history.map((h) => {
    const when = new Date(h.timestamp * 1000).toLocaleString();
    const mode = h.mode === 'permanent' ? 'Permanently deleted' : t('Moved to Recycle Bin');
    const extra = h.paths_truncated ? `<li>...and ${h.paths_truncated} more</li>` : '';
    const restored = (h.restored || []).length;
    const canRestore = h.mode !== 'permanent' && restored < h.paths.length;
    const errors = Array.isArray(h.errors) ? h.errors : [];
    const errorsOf = h.failed_count > errors.length ? ` (first ${errors.length} of ${h.failed_count})` : '';
    return `
      <li>
        <div class="history-row">
          <strong>${escapeHtml(when)}</strong>
          <span>${formatSize(h.freed_bytes)}</span>
        </div>
        <div class="history-meta">
          ${escapeHtml(mode)} &middot; ${h.deleted_count} item${h.deleted_count === 1 ? '' : 's'}
          ${h.failed_count ? `&middot; ${h.failed_count} failed` : ''} &middot; from ${escapeHtml(h.source)}
          ${restored ? `&middot; ${restored} restored` : ''}
        </div>
        ${canRestore ? `<button class="btn btn-secondary btn-sm btn-restore-cleanup" data-timestamp="${h.timestamp}">
          <i data-lucide="undo-2"></i> Restore</button>` : ''}
        <details>
          <summary>Show items</summary>
          <ul>${h.paths.map((p) => `<li>${escapeHtml(p)}</li>`).join('')}${extra}</ul>
        </details>
        ${errors.length ? `<details class="history-errors">
          <summary>Show what couldn't be removed${errorsOf}</summary>
          <ul class="errors-list">${errors.map((e) => `<li><span>${escapeHtml(e.path)}</span>
            <span class="error-reason">${escapeHtml(e.error)}</span></li>`).join('')}</ul>
        </details>` : ''}
      </li>
    `;
  }).join('');
  el.historyList.querySelectorAll('.btn-restore-cleanup').forEach((btn) => {
    btn.addEventListener('click', () => restoreCleanup(Number(btn.dataset.timestamp)));
  });
  lucide.createIcons();
}

async function restoreCleanup(timestamp) {
  try {
    const result = await apiJson('/api/history/restore', {
      method: 'POST',
      body: JSON.stringify({ timestamp }),
    });
    renderHistory(result.history);
    if (result.restored.length) {
      showToast(`Restored ${result.restored.length} item(s) to where they were. Scan again to see them.`, 'success');
    }
    if (result.errors.length) {
      showToast(`${plural(result.errors.length, 'item')} couldn't be restored`, 'error',
        { details: result.errors, detailsTitle: "Items that couldn't be restored" });
    }
  } catch (err) {
    showToast(`Could not restore: ${err.message}`, 'error');
  }
}

async function clearHistory() {
  try {
    renderHistory((await apiJson('/api/history', { method: 'DELETE' })).history);
  } catch (err) {
    showToast(`Could not clear history: ${err.message}`, 'error');
  }
}

// Only when the user clicks "Check for updates": JunkZero never checks on its own
async function checkForUpdates() {
  el.btnCheckUpdates.disabled = true;
  el.updateResult.textContent = 'Checking...';
  try {
    const res = await fetch('/api/update/check');
    if (res.status === 404) {
      el.updateResult.textContent = 'This copy of JunkZero cannot check for updates.';
      return;
    }
    const data = await res.json().catch(() => null);
    if (!res.ok) throw new Error(data?.detail || `Request failed (${res.status})`);
    const message = updateCheckMessage(data);
    el.updateResult.textContent = message.text;
    if (/^https:\/\//.test(message.url)) {
      const link = document.createElement('a');
      link.href = message.url;
      link.target = '_blank';
      link.rel = 'noopener noreferrer';
      link.textContent = 'Open the download page';
      el.updateResult.append(' ', link);
    }
  } catch (err) {
    el.updateResult.textContent = `Could not check for updates: ${err.message}`;
  } finally {
    el.btnCheckUpdates.disabled = false;
  }
}

async function copyDiagnostics() {
  try {
    const data = await apiJson('/api/diagnostics');
    await copyText(data.text || '');
    showToast('Diagnostics copied. Paste them into your bug report.', 'success');
  } catch (err) {
    showToast(`Could not copy diagnostics: ${err.message}`, 'error');
  }
}

// Windows junk only an administrator can remove (Windows Update downloads, Windows.old...):
// listed read-only with a pointer to Disk Cleanup. Hidden when there is none, or on servers
// that don't offer the list.
async function loadCleanupHints() {
  try {
    const res = await fetch('/api/system/cleanup-hints');
    if (!res.ok) return;
    const hints = ((await res.json()).hints || []).filter((h) => h && h.path);
    if (!hints.length) return;
    const size = (h) => (Number.isFinite(Number(h.size_bytes)) && h.size_bytes !== null ? Number(h.size_bytes) : null);
    const total = hints.reduce((acc, h) => acc + (size(h) || 0), 0);
    el.cleanupHintsTotal.textContent = total > 0 ? `(${formatSize(total)})` : '';
    el.cleanupHintsList.innerHTML = hints.map((h) => `
      <li>
        <div><strong>${escapeHtml(h.name || h.path)}</strong><div class="hint-path">${escapeHtml(h.path)}</div></div>
        <span>${size(h) === null ? '' : formatSize(size(h))}</span>
      </li>
    `).join('');
    el.cleanupHints.classList.remove('hidden');
    lucide.createIcons();
  } catch { /* not shown */ }
}

async function openRecycleBin() {
  try {
    await apiJson('/api/system/open-recycle-bin', { method: 'POST' });
  } catch (err) {
    showToast(err.message, 'error');
  }
}

// ==========================================
// Scheduled Report-Only Scans
// ==========================================

function updateScheduleDayVisibility() {
  el.scheduleDayLabel.classList.toggle('hidden', el.scheduleFrequency.value !== 'weekly');
}

function renderScheduleState(schedule, supported, lastReport) {
  if (!supported) {
    el.scheduleStatus.textContent = 'Scheduled scans need Windows Task Scheduler, so they are only available on Windows.';
  } else if (schedule.enabled) {
    const when = schedule.frequency === 'daily' ? 'every day' : `every ${el.scheduleDay.querySelector(`option[value="${schedule.day}"]`)?.textContent || schedule.day}`;
    el.scheduleStatus.textContent = `On: scans ${when} at ${schedule.time}.`;
  } else {
    el.scheduleStatus.textContent = 'Off. Save a schedule to have JunkZero scan and report automatically.';
  }
  el.btnSaveSchedule.disabled = !supported;
  el.btnDisableSchedule.classList.toggle('hidden', !schedule.enabled);

  if (lastReport) {
    const when = new Date(lastReport.generated_at * 1000).toLocaleString();
    el.lastReportText.textContent = `Last report (${when}): ${lastReport.item_count.toLocaleString()} items, ${formatSize(lastReport.total_bytes)} reclaimable.`;
    el.lastReportBox.classList.remove('hidden');
  } else {
    el.lastReportBox.classList.add('hidden');
  }
}

async function openScheduleModal() {
  el.scheduleModal.classList.remove('hidden');
  try {
    const data = await apiJson('/api/schedule');
    const s = data.schedule;
    el.scheduleFrequency.value = s.frequency;
    el.scheduleDay.value = s.day;
    el.scheduleTime.value = s.time;
    el.scheduleJunk.checked = s.include_junk_locations;
    el.schedulePaths.value = (s.paths.length ? s.paths : [el.targetPathInput.value.trim()].filter(Boolean)).join('\n');
    updateScheduleDayVisibility();
    renderScheduleState(s, data.supported, data.last_report);
  } catch (err) {
    showToast(`Could not load schedule: ${err.message}`, 'error');
  }
}

async function saveSchedule() {
  const payload = {
    frequency: el.scheduleFrequency.value,
    day: el.scheduleDay.value,
    time: el.scheduleTime.value,
    paths: el.schedulePaths.value.split('\n').map((p) => p.trim()).filter(Boolean),
    include_junk_locations: el.scheduleJunk.checked,
  };
  try {
    await apiJson('/api/schedule', { method: 'PUT', body: JSON.stringify(payload) });
    showToast('Schedule saved. Scheduled scans only report; nothing is deleted.', 'success');
    await openScheduleModal();
  } catch (err) {
    showToast(`Could not save schedule: ${err.message}`, 'error');
  }
}

async function disableSchedule() {
  try {
    await apiJson('/api/schedule', { method: 'DELETE' });
    showToast('Scheduled scans turned off', 'success');
    await openScheduleModal();
  } catch (err) {
    showToast(`Could not turn off schedule: ${err.message}`, 'error');
  }
}

async function loadLatestReport() {
  try {
    const report = await apiJson('/api/reports/latest');
    state.items = report.items;
    markExcluded(state.items);
    state.selectedIds.clear();
    resetResultViews();
    report.items.forEach((i) => { if (i.selected && !i.excluded_by) state.selectedIds.add(i.id); });
    updateMetrics({
      files_scanned: 0,
      dirs_scanned: 0,
      garbage_count: report.item_count,
      garbage_bytes: report.total_bytes,
      category_counts: report.category_counts,
      category_bytes: report.category_bytes,
    });
    applyFiltersAndRender(true);
    renderSmartSummary();
    el.scheduleModal.classList.add('hidden');
    const note = report.items_truncated ? ` (largest ${report.items.length.toLocaleString()} shown)` : '';
    showToast(`Loaded report from ${new Date(report.generated_at * 1000).toLocaleString()}${note}. Review before deleting.`, 'info');
  } catch (err) {
    showToast(`Could not load report: ${err.message}`, 'error');
  }
}

// ==========================================
// Remembered Preferences (theme, target folder, scan toggles)
// ==========================================

function currentScanOptions() {
  return Object.fromEntries(Object.entries(SCAN_OPTION_INPUTS).map(([key, input]) => [key, input.checked]));
}

function applyTheme(theme) {
  const isDark = theme !== 'light';
  document.body.classList.toggle('dark-theme', isDark);
  document.body.classList.toggle('light-theme', !isDark);
  el.themeIcon.setAttribute('data-lucide', isDark ? 'sun' : 'moon');
  lucide.createIcons();
}

async function loadPreferences() {
  let prefs;
  try {
    prefs = (await apiJson('/api/preferences')).preferences || {};
  } catch (err) {
    return; // Defaults stay in place
  }
  if (prefs.theme) applyTheme(prefs.theme);
  Object.entries(prefs.scan_options || {}).forEach(([key, value]) => {
    if (SCAN_OPTION_INPUTS[key]) SCAN_OPTION_INPUTS[key].checked = Boolean(value);
  });
  if (Array.isArray(prefs.file_types)) state.fileTypes = parseFileTypes(prefs.file_types.join(' ')).types;
  state.fileTypeMinDays = Math.max(0, Math.floor(Number(prefs.file_type_min_days) || 0));
  el.fileTypeMinDays.value = String(state.fileTypeMinDays);
  updateFileTypesHint();
  if (prefs.target_path) {
    el.targetPathInput.value = prefs.target_path;
    syncDrivePills();
  }
}

async function savePreferences() {
  const payload = {
    theme: document.body.classList.contains('light-theme') ? 'light' : 'dark',
    target_path: el.targetPathInput.value.trim(),
    scan_options: currentScanOptions(),
    file_types: state.fileTypes,
    file_type_min_days: state.fileTypeMinDays,
  };
  try {
    await apiJson('/api/preferences', { method: 'PUT', body: JSON.stringify(payload) });
  } catch (err) {
    console.warn('Could not save preferences:', err);
  }
}

// ==========================================
// File Type Search (results go to the deletion list as "Chosen File Types")
// ==========================================

function updateFileTypesHint() {
  const n = state.fileTypes.length;
  el.fileTypesHint.textContent = n ? plural(n, 'type') : 'none picked';
  el.fileTypesHint.parentElement.title = n
    ? `Also list: ${state.fileTypes.map((t) => '.' + t).join(', ')}`
    : 'Also list files of the types you picked in File Types';
}

async function openFileTypesModal() {
  el.fileTypesModal.classList.remove('hidden');
  el.fileTypeInput.value = '';
  el.fileTypeMinDays.value = String(state.fileTypeMinDays);
  if (!state.fileTypePresets.length) {
    try {
      const data = await apiJson('/api/file-types/presets');
      state.fileTypePresets = data.presets || [];
      state.fileTypesNeverListed = data.never_listed || [];
    } catch (err) {
      showToast(`Could not load file type groups: ${err.message}`, 'error');
    }
  }
  renderFileTypes();
  el.fileTypeInput.focus();
}

function closeFileTypesModal() {
  el.fileTypesModal.classList.add('hidden');
}

function renderFileTypes() {
  el.fileTypePresets.innerHTML = state.fileTypePresets.map((p) => `
    <button type="button" class="checkbox-tag btn-file-type-preset" data-preset="${escapeHtml(p.id)}"
      aria-pressed="${presetState(p.types, state.fileTypes)}"
      title="${escapeHtml(p.types.map((t) => '.' + t).join(' '))}">${escapeHtml(p.label)}</button>
  `).join('');
  el.fileTypePresets.querySelectorAll('.btn-file-type-preset').forEach((btn) => {
    btn.addEventListener('click', () => togglePreset(btn.dataset.preset));
  });

  const n = state.fileTypes.length;
  el.fileTypeCount.textContent = n ? plural(n, 'type') : '';
  el.btnSearchFileTypes.disabled = !n;
  el.fileTypeChips.innerHTML = n
    ? state.fileTypes.map((t) => `
      <span class="type-chip">.${escapeHtml(t)}
        <button type="button" class="btn-remove-file-type" data-type="${escapeHtml(t)}"
          aria-label="Remove .${escapeHtml(t)}" title="Remove"><i data-lucide="x"></i></button>
      </span>`).join('')
    : '<span class="list-empty">No file types yet. Pick a group above or type your own.</span>';
  lucide.createIcons();
  el.fileTypeChips.querySelectorAll('.btn-remove-file-type').forEach((btn) => {
    btn.addEventListener('click', () => setFileTypes(state.fileTypes.filter((t) => t !== btn.dataset.type)));
  });
}

function setFileTypes(types) {
  state.fileTypes = types;
  updateFileTypesHint();
  renderFileTypes();
  savePreferences();
}

function togglePreset(id) {
  const preset = state.fileTypePresets.find((p) => p.id === id);
  if (!preset) return;
  if (presetState(preset.types, state.fileTypes) === 'true') {
    setFileTypes(state.fileTypes.filter((t) => !preset.types.includes(t)));
  } else {
    setFileTypes([...state.fileTypes, ...preset.types.filter((t) => !state.fileTypes.includes(t))]);
  }
}

function addFileTypes(text) {
  const parsed = parseFileTypes(text);
  const { invalid } = parsed;
  const refused = parsed.types.filter((t) => state.fileTypesNeverListed.includes(t.split('.').pop()));
  const types = parsed.types.filter((t) => !refused.includes(t));
  if (invalid.length) {
    showToast(`Not a file type: ${invalid.join(', ')}. Use an extension such as .mp4`, 'error');
  } else if (refused.length) {
    showToast(`${refused.map((t) => '.' + t).join(', ')}: backup images and virtual machine disks are never listed`, 'error');
  }
  if (!types.length) return;
  setFileTypes([...state.fileTypes, ...types.filter((t) => !state.fileTypes.includes(t))]);
  el.fileTypeInput.value = invalid.join(' ');
}

// ==========================================
// Largest Files & Folders (read-only)
// ==========================================

state.largest = null;

async function openLargestModal() {
  const path = el.targetPathInput.value.trim();
  if (!path) {
    showToast('Please specify a folder first', 'error');
    return;
  }
  state.largest = null;
  el.largestModal.classList.remove('hidden');
  el.largestSubtitle.textContent = `Measuring ${path}...`;
  el.largestList.innerHTML = '<li class="list-empty"><div class="spinner"></div> Measuring folder sizes. Large drives can take a minute.</li>';
  el.btnLargest.disabled = true;
  try {
    const data = await apiJson('/api/space/largest', { method: 'POST', body: JSON.stringify({ path }) });
    state.largest = data;
    const partial = data.complete ? '' : ' (stopped early on a very large folder; sizes may be low)';
    el.largestSubtitle.textContent =
      `${data.total_formatted} in ${data.file_count.toLocaleString()} files under ${data.root}${partial}`;
    renderLargest('folders');
  } catch (err) {
    el.largestSubtitle.textContent = 'Could not measure this folder';
    el.largestList.innerHTML = `<li class="list-empty">${escapeHtml(err.message)}</li>`;
  } finally {
    el.btnLargest.disabled = false;
  }
}

function renderLargest(tab) {
  el.largestTabFolders.classList.toggle('active', tab === 'folders');
  el.largestTabFiles.classList.toggle('active', tab === 'files');
  el.largestTabFolders.setAttribute('aria-selected', String(tab === 'folders'));
  el.largestTabFiles.setAttribute('aria-selected', String(tab === 'files'));
  if (!state.largest) return;

  const rows = tab === 'folders' ? state.largest.folders : state.largest.files;
  if (!rows.length) {
    el.largestList.innerHTML = `<li class="list-empty">No ${tab} found</li>`;
    return;
  }
  const max = rows[0].size_bytes || 1;
  el.largestList.innerHTML = rows.map((r) => {
    const meta = tab === 'folders'
      ? `${r.file_count.toLocaleString()} file${r.file_count === 1 ? '' : 's'}`
      : `Modified ${escapeHtml(r.modified_date)}`;
    return `
      <li>
        <div class="largest-main">
          <div class="history-row">
            <strong class="largest-name" title="${escapeHtml(r.path)}">${escapeHtml(r.name)}</strong>
            <span>${escapeHtml(r.size_formatted)}</span>
          </div>
          <div class="largest-bar"><span style="width: ${Math.max(1, (r.size_bytes / max) * 100).toFixed(1)}%"></span></div>
          <div class="history-meta">${meta} &middot; ${escapeHtml(r.path)}</div>
        </div>
        <div class="row-actions">
          <button class="action-icon-btn btn-largest-inspect" title="Review in folder explorer" data-path="${escapeHtml(r.path)}">
            <i data-lucide="folder-tree"></i>
          </button>
          <button class="action-icon-btn btn-largest-open" title="${t('Show in Windows Explorer')}" data-path="${escapeHtml(r.path)}">
            <i data-lucide="folder"></i>
          </button>
        </div>
      </li>
    `;
  }).join('');
  lucide.createIcons();

  el.largestList.querySelectorAll('.btn-largest-inspect').forEach((btn) => {
    btn.addEventListener('click', () => openHierarchyModal(btn.dataset.path));
  });
  el.largestList.querySelectorAll('.btn-largest-open').forEach((btn) => {
    btn.addEventListener('click', async () => {
      try {
        await apiJson('/api/system/open-explorer', { method: 'POST', body: JSON.stringify({ path: btn.dataset.path }) });
      } catch (err) {
        showToast('Could not open folder in Explorer', 'error');
      }
    });
  });
}

window.addEventListener('DOMContentLoaded', initApp);
