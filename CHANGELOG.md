# Changelog

## [1.2.0] - 2026-10-16

### Permission problems fixed or explained
- Permanent deletes no longer stop at read-only files or folders that are your own (the read-only attribute on Windows, a read-only folder on a Mac or Linux): JunkZero makes them writable and finishes. It never does this to what belongs to another account or the system, or to what you locked.
- When a delete is refused, it says why in plain words and what fixes it: macOS privacy settings (with the app to give Full Disk Access and an **Open Privacy & Security** button), a file locked in Finder (how to unlock it), a file that belongs to another account or the system (JunkZero doesn't use administrator rights), a folder you can't change, a read-only disk, or Windows denying access. These messages stay on screen until dismissed.
- Scans report the folders they weren't allowed to read instead of skipping them silently, and on a Mac tell macOS privacy blocks apart, with the same button.

### Deletes that fit the disk
- JunkZero now tells you, before deleting, what will happen on each disk, by name and format: "J Red (exFAT): 12 items go to a hidden JunkZero folder on that disk for 7 days instead of the Trash, because on exFAT disks macOS may erase items instead of keeping them in the Trash." When every selected item goes to the holding folder, the dialog's message, mode and button say so.
- A note under the scan location says the same as soon as you pick a disk whose deletes won't use the Trash, or that is read-only.
- Read-only disks (a CD or DVD, or an NTFS disk on a Mac, which macOS can only read) are named as such, and nothing is attempted there; when every selected item is on one, the delete button stays off.
- The delete does exactly what the dialog said: Windows fixed drives and APFS/Mac OS Extended disks use the Recycle Bin or Trash; USB drives, memory cards, network drives, RAM disks, and on a Mac exFAT, FAT and NTFS disks use the holding folder; items too big for a Windows drive's Recycle Bin, or on one set to erase files at once, also go to the holding folder, with the reason given.

### Exclude from the results table
- Each result has an **Exclude** checkbox before its category. Ticking it adds the item to Exclusions: the row is greyed out and can't be ticked for deletion, and later scans don't list it until it is removed under **Exclusions** at the top. Unticking undoes it. This replaces the hide icon in the row's actions.
- Excluded items can't be deleted from any screen, including Folder Explorer, and neither can a folder that holds one; the delete says which exclusion is in the way.

### Results while the scan runs
- Found items appear in the results table while a scan or file type search is still running (refreshed twice a second), instead of only when it finishes. They can be inspected, ticked and deleted right away; empty folders and duplicates still appear at the end, since finding them needs the whole scan.

### Mac fixes
- Deleting from an exFAT, FAT or NTFS disk (most USB drives and memory cards, and disks shared with Windows) no longer risks erasing items for good. On those disks macOS can erase an item at once when asked to move it to the Trash, so JunkZero now keeps it in its hidden holding folder on that disk for 7 days instead, restorable from Cleanup History, as it already did for network drives. Only the startup disk and APFS or Mac OS Extended disks use the Trash.
- Carbon Copy Cloner's `_CCC SafetyNet` folder counts as a backup: it is never scanned or deleted from.
- Deleting to the Trash no longer fails with "Insufficient access privileges for operation" when running from source. `requirements.txt` now includes `pyobjc-framework-Cocoa` on macOS, which lets the Trash use Apple's current API, and `run.command` installs anything missing on launch (creating `.venv` the first time). If the package is still missing, JunkZero says so when it starts and in the delete error, with the command that fixes it.
- Browse opens the folder picker in front instead of behind the browser, starts at the current scan location (or without one, if macOS won't open it there), and says when the picker can't open. In JunkZero's own window it uses the window's folder picker.
- The drive button is highlighted only while the scan location is that whole drive, so a chosen subfolder no longer looks like the drive is still selected.

### Search by file type
- New **File Types** window: pick groups such as Videos, Photos, Documents or Archives, or type your own extensions (`.mp4`, `*.mkv`, `tar.gz`), and optionally only files unchanged for a number of days. **Search these types** lists every matching file in the scan location under "Chosen File Types", ready to tick and delete. A search replaces the current results, and can't be started while a scan is running.
- Found files are Review Recommended and never preselected. Exclusions, protected system folders, version-control and backup folders are skipped; personal and cloud files keep their warnings and DELETE typed. Backup images and virtual machine disks can't be searched for.
- **Chosen file types** under *Look for* adds the same search to every deep scan; there, files a built-in category already lists stay in that category. A file type search doesn't count as the last scan for "New since last scan" and teaches the smart score nothing.
- The chosen types and age are remembered with your other settings.

## [1.1.0] - 2026-10-11

### Fixes from a full audit
- The app token is no longer written into the page, where any program or other user account on the computer could fetch it. JunkZero's window gets it in its address instead, and reading the scan, history or reports now needs it too. `--mode server` prints the address to open.
- On Windows, items too big for the Recycle Bin, or on a drive whose Recycle Bin is set to remove files immediately, go to the holding folder for 7 days instead of being quietly erased.
- On a Mac, Restore puts back only the very item JunkZero moved to the Trash, not another file with the same or a similar name. Restore never moves anything over a file that appeared in the meantime.
- Two deletes at once can no longer remove every copy of a duplicate. A folder holding a `.git` (or a backup) is never deleted, and personal files inside a folder the scan wasn't sure about ask for DELETE typed.
- If JunkZero can't tell which files are open in other programs, it deletes nothing. On Windows, a folder with a file open inside is left whole instead of being half deleted.
- Only items rated Safe can be marked Delete; learning and bonuses no longer push Review items there, and an item is preselected only when it is marked Delete. "Select all safe" no longer ticks empty folders.
- The scan no longer follows Windows junctions out of the scanned folder, and doesn't scan inside `.git`, `.svn`, `.hg` or backup folders even when one is picked as the target. Empty Desktop, Music and other personal folders are never offered as empty folders.
- Scripts are listed as setup scripts only when a whole word of their name says setup or install (`research.sh` is no longer an installer). Compressed disk images (`.img.gz`, `.iso.xz`) may be backups and are no longer listed as setup archives.
- `.class` and `.pyc` files are Safe only inside build folders and `__pycache__`. `.war` and `.ear` files say why they are listed. Program Leftovers skips folders holding wallets, and folders too big to check fully. `Users/<name>/Library` on other disks (a migrated Mac) is protected like your own.
- AI Inspect is never surer than the scanner: build folders found by name and disk images are Review.
- "Show in Finder" on an app or package shows it instead of opening it.
- Settings and history can't be lost or mixed up when the app and a scheduled scan save at the same moment; a damaged settings file is kept aside instead of being overwritten. A scheduled scan no longer undoes schedule changes made while it ran. After a scan in the app, items from an older scheduled report can no longer be deleted.
- Releases are published only from main, never over an existing version, and build with pinned library versions. CI also tests Python 3.12, which builds the releases.
- Fixed a duplicates test that failed about once in 256 runs, and two tests that could time out on a busy machine.

### Safeguards for personal files
- Only JunkZero's own window can delete anything: every change needs a secret made fresh at each launch, so other web pages open in your browser can't ask JunkZero to delete files.
- Duplicates: the copy in Documents, Pictures, Desktop or a cloud folder is kept over the one in Downloads or a temp folder, and a copy is deleted only while another identical copy is still there, so the last copy is never removed.
- Files in OneDrive, Dropbox, Google Drive, iCloud Drive and Box are never preselected or marked Delete, and say that deleting them also deletes the cloud copy. Online-only cloud files are never listed or downloaded.
- Backup folders (File History, Windows system images, Time Machine), backup images and virtual machine disks are never listed. `.log` and `.tmp` files outside temp, cache, log and build folders are no longer listed.
- On USB sticks, memory cards and network drives, which have no Recycle Bin, deleted items are kept in a hidden JunkZero folder on that drive for 7 days instead of being erased.
- Documents, photos, videos, game saves and anything changed in the last 24 hours are never preselected or marked Delete. Program Leftovers holding personal files are left out.
- The delete dialog shows what will be removed (personal files, cloud items, top folders, file types). Deletes with personal files, over 5 GB or over 1,000 items need DELETE typed. Folder Explorer counts what is inside a folder before deleting it.
- Cleanup History has a Restore button that puts items back from the Recycle Bin, Trash or holding folder, without overwriting anything.
- Files open in Office, LibreOffice or another program are skipped.

### Only setup programs count as installers
- An `.exe` is no longer listed just because it sits in Downloads, Desktop or temp, or anywhere in your user folder. JunkZero looks inside it and lists it only when it is a setup program: built with Inno Setup, NSIS, InstallShield, WiX, Squirrel, Advanced Installer and similar tools, or described as a setup or installer in its version info.
- Portable apps, tools, games and uninstallers are left off the list, and so are programs in Program Files, AppData or a folder with the app's .dll files.
- The Smart Score says what was found (for example "+15: setup program: built with Inno Setup"). An `.exe` only named like a setup file gets "-10: only its name says setup". The file inspector explains any `.exe` the same way.

### Smarter setup archives
- A `.zip` (or `.tar.gz`) is no longer treated as an installer because of its name or because it sits in Downloads. JunkZero reads the archive's file list, without unpacking it, and lists it under Installers only when it holds a setup program, installer package, disk image or portable app and nothing personal.
- Archives with documents, photos, videos, spreadsheets or other personal files are never listed. Password-protected, damaged, `.rar` and `.7z` archives are never listed either.
- Setup archives stay Review Recommended (never preselected). Their Smart Score says what was found inside, for example "looked inside: setup.exe inside, nothing personal", and the file inspector shows the same.

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

### Empty Folders
- A folder that holds anything hidden is no longer listed as empty: dot-files and dot-folders, Windows hidden or system items, and items macOS Finder hides all count, at any depth. Hidden folders are never offered as empty themselves.
- Just before deleting an empty folder, JunkZero checks again and skips it if a file or hidden folder appeared since the scan.

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
