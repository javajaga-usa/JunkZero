# Changelog

## [Unreleased]

### macOS
- JunkZero now runs on Macs (macOS 11 or later). Releases include `JunkZero-macOS.zip` with `JunkZero.app` next to `JunkZero.exe`; from source, double-click `run.command`.
- Quick Clean on a Mac covers app caches in `~/Library/Caches` (not the ones macOS keeps for itself), logs and crash reports, the temp folder, Xcode DerivedData, iOS Simulator caches and the npm cache.
- macOS system folders, the private parts of `~/Library` (Mail, Messages, Keychains, iCloud Drive, iPhone backups) and the insides of apps and Photos libraries are protected; the home folder, Library, Documents, Desktop, Movies and the other personal folders can't be deleted as a whole.
- Program Leftovers on a Mac finds folders in `~/Library/Application Support` of apps no longer in Applications.
- Deleted items go to the Trash, folder buttons open Finder, Browse uses the Finder folder picker, and scheduled scans run through launchd.
- Windows behavior is unchanged.

### Smarter results (all offline, no account or API key)
- Smart Score: every result gets a 1-99 score with a Delete / Review / Keep label. Click it to see the reasons. Smart Select ticks only the items marked Delete.
- JunkZero learns from your choices: kinds of items you delete score higher next time, and items you keep through repeated scans score lower. Cleanup History can forget what was learned.
- Program Leftovers (opt-in): app data folders left behind by programs you have uninstalled, unchanged for 180 days or more.
- A plain-language summary after each scan says what can go now, what to look at first, and what to keep.
- CSV exports include the score, label and reasons.

### Look and feel
- Redesigned interface in both themes: a compact header with the tools grouped together, one scan bar with the drives, folder and scan buttons on a single line, and short "Look for" chips that highlight when they are on (the full file types are in each chip's tooltip).
- Summary cards are smaller and sit beside one large total, so the results table starts higher on the screen. Clicking a card highlights it while it filters the table.
- The results table keeps fixed columns: sizes and dates no longer wrap, long names and paths are cut with "..." (hover for the full text), categories show a short label with the same color as the space bar, and the sorted column shows its direction.
- Dialogs share one style (header icon, close button, footer), lists have clear rows, the folder explorer uses a breadcrumb path, and messages show an icon for success or errors.
- The Inter font is now bundled with the app, so text looks the same offline and nothing is loaded from the internet. Native controls (dropdowns, time picker, scrollbars) follow the light or dark theme.

## [1.0.0] - 2026-10-10

The first release of JunkZero, a Windows app that finds junk files, shows you what they are, and moves the ones you pick to the Recycle Bin. It comes as a single `JunkZero.exe` that runs without installing Python.

### Finding junk
- Scans a folder or drive quickly and sorts what it finds into categories: installers, build leftovers (`node_modules`, `target`, `.class`, `__pycache__`), temp and log files, crash dumps, broken downloads, large files you haven't touched in six months, and empty folders.
- Optional categories you can switch on: duplicate files (the newest copy is kept), Old Downloads (files sitting in Downloads for 90 days or more), and your own Junk Rules (patterns such as `*.bak2` or whole folders, listed under "My Junk Rules").
- Quick Clean scans the usual junk spots in one click: your temp folder, app crash dumps, and Chrome, Edge, Brave and Firefox caches.
- "New since last scan" marks items that weren't there the last time you scanned the same folder, with a "New only" button to show just those.

### Seeing where the space goes
- Summary cards and a space bar split the reclaimable space by category; click a segment to filter the table.
- Junk by Folder groups results by the folder they're in, biggest first.
- Largest Files & Folders shows the biggest things in a folder even when they aren't junk.
- Search, sort and filter the results. Large scans stay responsive: the table shows 500 rows at a time with a "Show more" button.
- AI Inspect explains what a file is and whether it's safe to remove, offline or with a Gemini API key.

### Cleaning safely
- Deleted items go to the Recycle Bin unless you switch to Permanent mode, which asks for an extra confirmation. Every launch starts in Recycle Bin mode.
- Only items you can see and have ticked are deleted; anything hidden by a search or filter is left alone.
- Windows system folders, your user profile, and folders like Documents, Desktop and Downloads can't be deleted as a whole.
- Git, SVN and Mercurial folders are never scanned or deleted from, and empty folders are never ticked for you.
- File types that are also used for real work (`.obj`, `.pdb`, `.bak`, `.old` and similar) are only marked safe inside build output folders.
- Exclusions let you mark files, folders or patterns as "never flag".
- Cleanup History records every cleanup, with a shortcut to the Recycle Bin.

### Everyday use
- Scheduled scans run daily or weekly through Windows Task Scheduler and only save a report; they never delete anything.
- Export the results you're looking at to CSV.
- Light and dark themes. Your target folder, scan options and theme are remembered.
- Works fully offline, and the app's local server only answers requests from your own computer.
