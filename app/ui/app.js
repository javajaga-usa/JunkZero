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
  const headers = ['Name', 'Type', 'Category', 'Size (Bytes)', 'Size Formatted', 'Modified Date', 'Risk Level', 'Path', 'Reason'];
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

/**
 * JunkZero - Frontend Application Controller
 * High-performance file management, live scan streaming, multi-level folder hierarchy exploration,
 * and Recycle Bin (default) or permanent deletion.
 */

// Icons are bundled in /static/vendor; if the script fails to load the app must still work, just without icons
if (!window.lucide) window.lucide = { createIcons() {} };

// The table shows this many rows at a time ("Show more" adds another page), so large scans stay responsive
const TABLE_PAGE_SIZE = 500;
const SEARCH_DELAY_MS = 200;

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

function showToast(message, type = 'info') {
  const toast = document.createElement('div');
  toast.className = `toast toast-${type}`;
  const icon = { success: 'circle-check', error: 'circle-alert' }[type] || 'info';
  toast.innerHTML = `<i data-lucide="${icon}" class="toast-icon"></i><span></span>`;
  toast.querySelector('span').textContent = message;
  el.toastContainer.appendChild(toast);
  lucide.createIcons({ root: toast });
  setTimeout(() => {
    toast.style.opacity = '0';
    setTimeout(() => toast.remove(), 200);
  }, 3500);
}

// App Initialization
async function initApp() {
  lucide.createIcons();
  setupEventListeners();
  await loadAvailableDrives();
  await loadPreferences();
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
      const res = await fetch('/api/system/browse-folder', { method: 'POST' });
      const data = await res.json();
      if (data.path) {
        el.targetPathInput.value = data.path;
        showToast(`Target set to: ${data.path}`, 'success');
      }
    } catch (e) {
      showToast('Could not open folder picker', 'error');
    } finally {
      el.btnBrowse.disabled = false;
      el.btnBrowse.innerHTML = '<i data-lucide="folder-open"></i> Browse...';
      lucide.createIcons();
    }
  });

  // Start / Stop Scan Buttons
  el.btnStartScan.addEventListener('click', () => startScan());
  el.btnScanJunk.addEventListener('click', () => startScan({ junkOnly: true }));
  el.btnLargest.addEventListener('click', openLargestModal);
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
      if (checkAll) {
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
    state.filteredItems.forEach((i) => state.selectedIds.add(i.id));
    renderTable();
    updateSelectionSummary();
  });

  el.btnSelectAllSafe.addEventListener('click', () => {
    state.selectedIds.clear();
    state.items.forEach((i) => {
      if (i.risk_level === 'Safe') state.selectedIds.add(i.id);
    });
    renderTable();
    updateSelectionSummary();
    showToast('Selected all safe-to-delete items', 'success');
  });

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
      onConfirm: async () => {
        await executeBatchDelete(selectedItems);
      },
    });
  });

  el.btnExportReport.addEventListener('click', exportCsvReport);

  // Delete Mode Toggle
  el.modeRecycle.addEventListener('click', () => setDeleteMode(false));
  el.modePermanent.addEventListener('click', () => setDeleteMode(true));
  el.confirmPermanentAckInput.addEventListener('change', () => {
    el.btnExecuteDelete.disabled = !el.confirmPermanentAckInput.checked;
  });

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
      onConfirm: async () => {
        await executeDeleteFolder(folderPath);
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
      onConfirm: async () => {
        await executeBatchDelete([{ path: filePath, size_bytes: 0, is_directory: false }]);
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
      onConfirm: async () => {
        await executeBatchDelete(
          selectedEntries.map((e) => ({ path: e.path, size_bytes: e.size_bytes, is_directory: e.is_dir }))
        );
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
      try {
        await state.deleteConfirmCallback();
      } finally {
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
  el.btnOpenRecycleBin.addEventListener('click', openRecycleBin);

  // Schedule
  el.btnOpenSchedule.addEventListener('click', openScheduleModal);
  el.closeScheduleModal.addEventListener('click', () => el.scheduleModal.classList.add('hidden'));
  el.scheduleFrequency.addEventListener('change', updateScheduleDayVisibility);
  el.btnSaveSchedule.addEventListener('click', saveSchedule);
  el.btnDisableSchedule.addEventListener('click', disableSchedule);
  el.btnLoadReport.addEventListener('click', loadLatestReport);

  // Largest Files Modal
  el.closeLargestModal.addEventListener('click', () => el.largestModal.classList.add('hidden'));
  el.btnCloseLargest.addEventListener('click', () => el.largestModal.classList.add('hidden'));
  el.largestTabFolders.addEventListener('click', () => renderLargest('folders'));
  el.largestTabFiles.addEventListener('click', () => renderLargest('files'));
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
        document.querySelectorAll('.drive-pill').forEach((b) => b.classList.remove('active'));
        btn.classList.add('active');
        el.targetPathInput.value = d.drive;
      });

      el.driveButtons.appendChild(btn);
    });

    if (state.drives.length > 0) {
      el.targetPathInput.value = state.drives[0].drive;
    }

    lucide.createIcons();
  } catch (err) {
    console.error('Failed to load drives:', err);
  }
}

// Scanning Engine Controller
async function startScan({ junkOnly = false } = {}) {
  const targetPath = junkOnly ? '' : el.targetPathInput.value.trim();
  if (!targetPath && !junkOnly) {
    showToast('Please specify a valid path to scan', 'error');
    return;
  }

  state.items = [];
  state.filteredItems = [];
  state.selectedIds.clear();
  state.isScanning = true;
  resetResultViews();

  el.btnStartScan.classList.add('hidden');
  el.btnScanJunk.classList.add('hidden');
  el.btnStopScan.classList.remove('hidden');
  el.scanProgressContainer.classList.remove('hidden');
  el.statusText.textContent = junkOnly ? 'Scanning junk locations...' : 'Scanning in progress...';
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
  };
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

    state.eventSource = new EventSource('/api/scan/stream');
    let updateThrottleTimer = null;
    let latestStats = null;

    state.eventSource.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);

        if (data.type === 'item_found') {
          state.items.push(data.item);
          if (data.item.selected) {
            state.selectedIds.add(data.item.id);
          }

          latestStats = data.stats;
          if (!updateThrottleTimer) {
            updateThrottleTimer = setTimeout(() => {
              applyFiltersAndRender(false);
              updateMetrics(latestStats);
              updateThrottleTimer = null;
            }, 100);
          }
        } else if (data.type === 'completed') {
          // Drop any pending throttled update so it cannot overwrite the final stats
          clearTimeout(updateThrottleTimer);
          updateThrottleTimer = null;
          onScanCompleted(data.stats, data.changes);
        }
      } catch (e) {
        console.error('SSE parse error:', e);
      }
    };

    state.eventSource.onerror = () => {
      onScanCompleted();
    };
  } catch (err) {
    showToast(err.message, 'error');
    stopScan();
  }
}

async function stopScan() {
  try {
    await fetch('/api/scan/stop', { method: 'POST' });
  } catch (e) {}
  onScanCompleted();
}

function onScanCompleted(finalStats = null, changes = null) {
  state.isScanning = false;
  if (state.eventSource) {
    state.eventSource.close();
    state.eventSource = null;
  }

  el.btnStartScan.classList.remove('hidden');
  el.btnScanJunk.classList.remove('hidden');
  el.btnStopScan.classList.add('hidden');
  el.scanProgressContainer.classList.add('hidden');
  el.statusText.textContent = 'Scan Finished';
  document.querySelector('.status-dot').classList.remove('scanning');

  if (changes) setNewItems(changes);
  applyFiltersAndRender(true);
  if (finalStats) {
    updateMetrics(finalStats);
    let note = '';
    if (changes && changes.previous_scan_at) {
      const count = state.newPaths.size;
      note = count ? ` ${count.toLocaleString()} new since the last scan.` : ' Nothing new since the last scan.';
    }
    showToast(`Scan finished: ${finalStats.garbage_count} garbage items detected!${note}`, 'success');
  } else {
    showToast(`Scan stopped. Found ${state.items.length} items.`, 'info');
  }
}

function updateMetrics(stats) {
  if (!stats) return;

  el.scanMetricsFiles.textContent = `${stats.files_scanned.toLocaleString()} files scanned`;
  el.scanMetricsDirs.textContent = `${stats.dirs_scanned.toLocaleString()} folders`;
  el.scanMetricsRate.textContent = `${stats.garbage_count.toLocaleString()} found (${formatSize(stats.garbage_bytes)})`;

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
        <td colspan="9">
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
    const isChecked = state.selectedIds.has(item.id);
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
      <tr class="${isChecked ? 'row-selected' : ''}" data-id="${escapeHtml(item.id)}">
        <td>
          <input type="checkbox" class="row-checkbox" data-id="${escapeHtml(item.id)}" ${isChecked ? 'checked' : ''} />
        </td>
        <td>
          <i data-lucide="${typeIcon}" class="file-type-icon ${typeClass}"></i>
        </td>
        <td>
          <div class="file-name-cell clickable-file-cell" title="Click to view parent folder and hierarchy" data-path="${escapeHtml(item.path)}">
            <span class="file-name-text">${escapeHtml(item.name)}</span>${state.newPaths.has(item.path) ? '<span class="new-badge" title="Not found by the previous scan of this folder">New</span>' : ''}
          </div>
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
          <div class="file-path-cell" title="${escapeHtml(item.path)}">${escapeHtml(item.path)}</div>
        </td>
        <td>
          <div class="row-actions">
            <button class="action-icon-btn btn-inspect-hierarchy" title="Explore parent folder hierarchy" data-path="${escapeHtml(item.path)}">
              <i data-lucide="folder-tree"></i>
            </button>
            <button class="action-icon-btn btn-open-folder" title="Open containing folder in Windows Explorer" data-path="${escapeHtml(item.path)}">
              <i data-lucide="folder"></i>
            </button>
            <button class="action-icon-btn ai-btn btn-inspect-ai" title="AI Inspection & Advice" data-path="${escapeHtml(item.path)}">
              <i data-lucide="bot"></i>
            </button>
            <button class="action-icon-btn btn-exclude" title="Never flag this ${item.is_directory ? 'folder' : 'file'} again" data-path="${escapeHtml(item.path)}">
              <i data-lucide="eye-off"></i>
            </button>
          </div>
        </td>
      </tr>
    `;
  }).join('');

  const remaining = state.filteredItems.length - shown.length;
  const moreRow = remaining > 0 ? `
      <tr class="more-row">
        <td colspan="9">
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

    const exclude = e.target.closest('.btn-exclude');
    if (exclude) {
      e.stopPropagation();
      addExclusion(exclude.dataset.path, { fromTable: true });
      return;
    }

    const ai = e.target.closest('.btn-inspect-ai');
    if (ai) {
      e.stopPropagation();
      openAiInspector(ai.dataset.path);
    }
  });
}

// Ticked items that the current filters show (the ones "Delete Selected" removes)
function visibleSelectedItems() {
  return state.filteredItems.filter((i) => state.selectedIds.has(i.id));
}

// Update Footer Selection Summary
function updateSelectionSummary() {
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
    const allFilteredChecked = state.filteredItems.every((i) => state.selectedIds.has(i.id));
    const someFilteredChecked = state.filteredItems.some((i) => state.selectedIds.has(i.id));

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
        onConfirm: async () => {
          await executeBatchDelete([{ path, size_bytes: 0, is_directory: isDir }]);
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
async function executeDeleteFolder(folderPath) {
  try {
    const res = await fetch('/api/filesystem/delete-folder', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path: folderPath, permanent: state.permanentDelete }),
    });

    const result = await res.json();
    if (result.deleted_count > 0) {
      const verb = result.mode === 'permanent' ? 'Permanently deleted' : 'Moved to Recycle Bin';
      showToast(`${verb}: ${folderPath}`, 'success');

      // Purge any items from main scan that were inside this deleted folder
      const normPrefix = folderPath.toLowerCase().replace(/\\/g, '/');
      state.items = state.items.filter((i) => !i.path.toLowerCase().replace(/\\/g, '/').startsWith(normPrefix));
      applyFiltersAndRender(true);

      // Navigate to parent folder or close if at root
      if (state.currentHierarchy && state.currentHierarchy.parent_path) {
        await loadHierarchy(state.currentHierarchy.parent_path);
      } else {
        closeHierarchyModal();
      }
    } else if (result.failed_count > 0) {
      showToast(`Could not delete folder: ${result.errors[0]?.error || 'Locked or permission denied'}`, 'error');
    }
  } catch (err) {
    showToast(`Folder deletion failed: ${err.message}`, 'error');
  }
}

// Batch Deletion (Recycle Bin or permanent, per the current delete mode)
async function executeBatchDelete(items) {
  try {
    const res = await fetch('/api/clean', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        items,
        permanent: state.permanentDelete,
      }),
    });

    const result = await res.json();

    if (result.deleted_count > 0) {
      if (result.mode === 'permanent') {
        showToast(`Permanently deleted ${result.deleted_count} item(s) (freed ${result.freed_formatted})!`, 'success');
      } else {
        showToast(`Moved ${result.deleted_count} item(s) (${result.freed_formatted}) to the Recycle Bin. Empty it to free the space.`, 'success');
      }

      const deletedPaths = new Set(items.map((i) => i.path));
      state.items = state.items.filter((i) => !deletedPaths.has(i.path));
      items.forEach((i) => state.selectedIds.delete(i.id));

      applyFiltersAndRender(true);
      updateSelectionSummary();
    }

    if (result.failed_count > 0) {
      showToast(`${result.failed_count} item(s) could not be deleted (locked or protected).`, 'error');
    }
  } catch (err) {
    showToast(`Deletion failed: ${err.message}`, 'error');
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
    : '<i data-lucide="trash-2"></i> Move Selected to Recycle Bin';
  lucide.createIcons();
}

// Universal Deletion Confirmation Modal
function requestDeleteConfirmation({ title, subtitle, count, size, onConfirm }) {
  const permanent = state.permanentDelete;
  el.confirmModalTitle.textContent = title;
  el.confirmModalSubtitle.textContent = subtitle;
  el.confirmCount.textContent = count.toLocaleString();
  el.confirmSize.textContent = size;

  el.confirmModalIcon.className = permanent ? 'modal-icon danger' : 'modal-icon warning';
  el.confirmWarningBanner.classList.toggle('danger-banner', permanent);
  el.confirmMode.classList.toggle('text-danger', permanent);

  if (permanent) {
    el.confirmMode.textContent = 'Permanent (No Recycle Bin)';
    el.confirmWarningText.textContent = 'Selected files/folders will be PERMANENTLY erased from disk immediately. This cannot be undone.';
    el.btnExecuteDelete.innerHTML = '<i data-lucide="trash-2"></i> Permanently Delete Now';
  } else {
    el.confirmMode.textContent = 'Recycle Bin';
    el.confirmWarningText.textContent = 'Selected files/folders will be moved to the Recycle Bin. You can restore them from there until it is emptied.';
    el.btnExecuteDelete.innerHTML = '<i data-lucide="recycle"></i> Move to Recycle Bin';
  }

  // Permanent mode needs an explicit second confirmation before the button unlocks
  el.confirmPermanentAckInput.checked = false;
  el.confirmPermanentAck.classList.toggle('hidden', !permanent);
  el.btnExecuteDelete.disabled = permanent;

  state.deleteConfirmCallback = onConfirm;

  el.confirmModal.classList.remove('hidden');
  lucide.createIcons();
}

function closeConfirmModal() {
  el.confirmModal.classList.add('hidden');
  state.deleteConfirmCallback = null;
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

function pathIsWithin(path, root) {
  const norm = (p) => p.toLowerCase().replace(/\\/g, '/').replace(/\/+$/, '');
  const a = norm(path);
  const b = norm(root);
  return a === b || a.startsWith(`${b}/`);
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
        renderExclusions((await apiJson('/api/exclusions', {
          method: 'PUT', body: JSON.stringify({ rules: remaining }),
        })).rules);
      } catch (err) {
        showToast(`Could not remove exclusion: ${err.message}`, 'error');
      }
    });
  });
}

async function addExclusion(rule, { fromTable = false } = {}) {
  rule = (rule || '').trim();
  if (!rule) return;
  try {
    const data = await apiJson('/api/exclusions/add', { method: 'POST', body: JSON.stringify({ rule }) });
    el.exclusionInput.value = '';
    if (!el.exclusionsModal.classList.contains('hidden')) renderExclusions(data.rules);
    if (fromTable) {
      // Hide the excluded item (and anything inside it) from the current results
      const removed = state.items.filter((i) => pathIsWithin(i.path, rule));
      state.items = state.items.filter((i) => !pathIsWithin(i.path, rule));
      removed.forEach((i) => state.selectedIds.delete(i.id));
      applyFiltersAndRender(true);
      showToast(`Excluded ${rule.split(/[\\/]/).pop()}. It won't be flagged in future scans.`, 'success');
    }
  } catch (err) {
    showToast(`Could not add exclusion: ${err.message}`, 'error');
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
        <button class="action-icon-btn btn-folder-open" title="Show in Windows Explorer" data-path="${escapeHtml(g.folder)}">
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
  } catch (err) {
    showToast(`Could not load history: ${err.message}`, 'error');
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
    const mode = h.mode === 'permanent' ? 'Permanently deleted' : 'Moved to Recycle Bin';
    const extra = h.paths_truncated ? `<li>...and ${h.paths_truncated} more</li>` : '';
    return `
      <li>
        <div class="history-row">
          <strong>${escapeHtml(when)}</strong>
          <span>${formatSize(h.freed_bytes)}</span>
        </div>
        <div class="history-meta">
          ${escapeHtml(mode)} &middot; ${h.deleted_count} item${h.deleted_count === 1 ? '' : 's'}
          ${h.failed_count ? `&middot; ${h.failed_count} failed` : ''} &middot; from ${escapeHtml(h.source)}
        </div>
        <details>
          <summary>Show items</summary>
          <ul>${h.paths.map((p) => `<li>${escapeHtml(p)}</li>`).join('')}${extra}</ul>
        </details>
      </li>
    `;
  }).join('');
}

async function clearHistory() {
  try {
    renderHistory((await apiJson('/api/history', { method: 'DELETE' })).history);
  } catch (err) {
    showToast(`Could not clear history: ${err.message}`, 'error');
  }
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
    state.selectedIds.clear();
    resetResultViews();
    report.items.forEach((i) => { if (i.selected) state.selectedIds.add(i.id); });
    updateMetrics({
      files_scanned: 0,
      dirs_scanned: 0,
      garbage_count: report.item_count,
      garbage_bytes: report.total_bytes,
      category_counts: report.category_counts,
      category_bytes: report.category_bytes,
    });
    applyFiltersAndRender(true);
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
  if (prefs.target_path) {
    el.targetPathInput.value = prefs.target_path;
    document.querySelectorAll('.drive-pill').forEach((b) => {
      b.classList.toggle('active', b.dataset.path.toLowerCase() === prefs.target_path.toLowerCase());
    });
  }
}

async function savePreferences() {
  const payload = {
    theme: document.body.classList.contains('light-theme') ? 'light' : 'dark',
    target_path: el.targetPathInput.value.trim(),
    scan_options: currentScanOptions(),
  };
  try {
    await apiJson('/api/preferences', { method: 'PUT', body: JSON.stringify(payload) });
  } catch (err) {
    console.warn('Could not save preferences:', err);
  }
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
          <button class="action-icon-btn btn-largest-open" title="Show in Windows Explorer" data-path="${escapeHtml(r.path)}">
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
