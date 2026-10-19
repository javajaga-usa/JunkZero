# Changelog

## [1.3.1] - 2026-10-19

Fixes from a full audit, and the Linux fixes made after 1.3.0. Downloads: Windows `JunkZero-Setup.exe` (or the portable zip), Mac `JunkZero-macOS.dmg`, Linux `junkzero_1.3.1_amd64.deb` (or the tar.gz). The apps are not code-signed, so Windows SmartScreen and macOS Gatekeeper warn on first start.

### Safer detection
- **A stray project file no longer makes personal files junk**: a `package.json` left in your home folder made `Release`, `Debug`, `bin` and `out` folders below it count as build output, so a 3D model in `Documents/Models/Release` was ticked. Project files in the home folder, a drive's top folder or Documents/Desktop/Downloads don't count any more, and `Debug`, `Release`, `x64`, `x86` count only next to a C, C++ or .NET project.
- **Backup images and VM disks are never listed, as anything**: they were still offered as Old Downloads or as Duplicates of another copy.
- **Timeshift and Back In Time snapshots** on any disk are never scanned.
- **Hard-linked folder copies** (rsnapshot, `cp -al`) are not Duplicate Folders: deleting one frees nothing.
- **A Mac's `~/Library/Application Support`** is program data, like AppData on Windows: its sample libraries and models aren't Stale Large Files or Duplicates.
- **Zipped disk images** (a zipped SD-card backup) are no longer called setup archives.
- **A folder that can't be read fully** makes a Program Leftover or Old Project "unknown", so it isn't listed.
- **A duplicate folder holding junk that is listed on its own** isn't offered whole as well, so nothing is counted twice.
- **AI Inspect is never surer than the scanner**: installers, setup archives, `.msi`, `.apk`, files changed in the last 24 hours and cloud files say Review Carefully.
- A file dated far in the future no longer stops the scan of its folder.

### Deleting and Restore
- **Restore brings back only what that cleanup deleted**: on Windows and Linux it could bring back a later item from the same place.
- **Files open in another program are found under linked folders too**, including a Mac's temp folder, so they aren't deleted.
- **The Recycle Bin size check uses the size at delete time**, so a folder that grew since the scan isn't quietly erased by Windows.
- **A scheduled report opened after a scan can be deleted from** (every item used to fail with "Not in the latest scan results").
- A Windows permanent delete that fails partway leaves the folder under its own name.
- The holding-folder clean-up no longer forgets a USB drive added while it runs.

### Permission problems
- **Junk you can't remove isn't listed**: a file of another account's (left by `sudo`) in your own folder, and a folder holding another account's folder (npm's cache after `sudo npm`), are left out and counted in the note after the scan. Errors from inside a folder name the item that couldn't be removed.
- **Empty and the 7-day clean-up erase your own read-only folders** (files copied off a disc, Go's module cache) instead of stopping with "Directory not empty".
- **Restore explains what's wrong in plain words** when the folder it goes back into can't be changed, and makes your own read-only folder writable as deleting does.
- **On Linux, the Trash no longer keeps stray notes** for items in your own read-only folders.
- **The APT cache hint shows only when there are packages to clear** (it used to show on every Debian and MX Linux).
- **`run.sh` in a folder you can't write** (copied with `sudo`, or on a disk whose top folder belongs to root) says so and how to fix it, instead of asking you to install Python packages you have.
- **Accounts not allowed to use cron** (`/etc/cron.allow`) get a plain sentence saying so; a systemd timer still works, and the Schedule screen no longer says scheduled scans are Windows-only.
- On Windows, a network share whose top folder can't be changed gets its holding folder in the highest folder you can change.

### Scheduled scans
- **MX Linux**: a schedule saved while booted with systemd is made again in cron when the computer starts with SysVinit (MX's default); before, it silently stopped running.
- **Notifications after scheduled scans now appear under cron** (MX Linux).
- The systemd timer works when JunkZero's folder has `'` or `$` in its name, and scheduled scans keep a custom `JUNKZERO_DATA_DIR`.

### Smaller fixes
- Exclusions written as `~/Documents/Taxes` or `%USERPROFILE%\...` now work; a path that isn't a full path is refused with a reason.
- "Delete File Only" appears only for a file (it deleted the whole folder when the view was opened on a folder).
- "Show in Finder" always shows the item, never opens it (a `.pkg` launched Installer).
- AI Inspect and the open-folder button say what went wrong instead of showing nothing; the scan error message no longer repeats itself.
- README: when missed scheduled scans are made up, and the Mac `.dmg` in the release list.

### Fixed on Linux
- **Deleting from a Linux-formatted USB disk** (ext4 under `/media`) no longer fails for every item with "you don't have permission to change it". The top folder of such a disk belongs to root, so JunkZero couldn't make its holding folder there; it now uses the highest folder on that disk you can change, and Restore puts items back as before. Internal Linux disks whose top folder belongs to root use the holding folder too, since the Trash can't be made there.
- **Junk you can't remove isn't listed**: items in folders that belong to another account or the system are left out of the results, and a note after the scan says how many and why. Your own read-only folders still count, because JunkZero makes them writable before deleting.
- **`.war` and `.ear` files are listed only inside a project's build folder**: elsewhere (an old backup, a Tomcat `webapps` folder) they can be the only copy.
- **`run.sh` on a fresh MX Linux or Debian**: without `python3-venv` it now makes the environment with the system's pip; when something is missing it says what to install and keeps the terminal open (or shows a message box when started without one). README notes `bash run.sh` for when the file manager opens it in an editor.

## [1.3.0] - 2026-10-19

This release also includes everything listed under 1.2.0 below, which was never published on its own. Downloads: Windows `JunkZero-Setup.exe` (or the portable zip), Mac `JunkZero-macOS.dmg`, Linux `junkzero_1.3.0_amd64.deb` (or the tar.gz). The apps are not code-signed, so Windows SmartScreen and macOS Gatekeeper warn on first start.

### Easy start
- **Shortcuts for the portable downloads**: the Windows portable zip offers to add JunkZero to the Start menu and the desktop on first start, and the Linux tar.gz to the applications menu. **About** has the same buttons. When the portable folder is moved, the shortcuts follow it the next time JunkZero starts from the new place; shortcuts JunkZero didn't make are never changed.
- **Mac disk image**: releases add `JunkZero-macOS.dmg`, which shows JunkZero next to a shortcut to Applications to drag it onto. The zip stays.
- **`run.bat` sets itself up** like `run.command` and `run.sh`: the first double-click creates `.venv` and installs what JunkZero needs, says where to get Python if it's missing, and the console no longer waits for a key press after a normal exit.
- The README starts with a table of how to start JunkZero on each system.

### New
- **Space still held**: Cleanup History shows how much of what you deleted with JunkZero still sits in the Recycle Bin / Trash and the holding folders, and an **Empty** button erases only JunkZero's own items from cleanups at least N days old (7 by default). Nothing else in the Recycle Bin or Trash is touched, and only items JunkZero can point to exactly are counted (on a Mac, by file id).
- **Notifications after scheduled scans**: a scheduled scan that finds at least 500 MB (adjustable, or off, in Schedule) or fails shows a notification. On Windows clicking it opens the report in JunkZero; everywhere, the next launch offers a report it hasn't shown yet.
- **Chat and app caches**: Teams, Slack, Discord, Zoom logs, VS Code and Steam caches are offered from an exact folder list, only while that app is closed.
- **Duplicate folders** (with Duplicates): whole folders with exactly the same files, compared byte for byte; the copy in Documents, Pictures or a cloud folder is kept, and both are compared again before deleting.
- **Old coding projects** (opt-in): `node_modules`, `.venv`, `target`, `build`, `bin`/`obj` of projects untouched for 90 days, only next to the project file that rebuilds them. Review Recommended.
- **Installers for installed programs**: on Windows, a setup program for something already installed at the same or a newer version scores higher and says so.
- **Free space over time**: a per-disk chart in Cleanup History, with a line for each cleanup.
- **Map in Largest Files**: folders as boxes sized by their space; click to look inside.

### Linux (MX Linux, Debian, Ubuntu)
- JunkZero runs on Linux. Quick Clean lists only known caches apps rebuild themselves (thumbnails, browser caches, pip, Yarn, Go, npm, Mesa and NVIDIA shader caches); system folders and the hidden settings folders in your home folder are never scanned or deleted.
- Deleted items go to the freedesktop.org Trash, so Restore in Cleanup History works; USB drives, Windows partitions and network shares use the 7-day holding folder.
- Browse uses zenity, kdialog or yad; scheduled scans use a systemd user timer, or cron where the system starts without systemd (MX Linux's default). The APT cache and system journal are shown read-only with the command that clears them.
- Releases add `junkzero_<version>_amd64.deb` (menu entry and `junkzero` command; uninstalling also removes the scheduled scan) and a portable `JunkZero-Linux-x86_64.tar.gz`. From source, run `./run.sh`.

### Safer detection
- Browser and app databases are no longer listed: `.log` files inside Chrome/Edge extension storage, IndexedDB, Local Storage and Electron apps (VS Code, Postman, MetaMask) hold the newest data, not logs. Folders named `local`, `appdata` or starting with a dot no longer count as temp folders on their own.
- Empty folders are never marked Delete, so Smart Select no longer ticks an empty folder tree such as `Photos/2025 trip`.
- Crypto wallets and keys are treated as personal: folders whose names contain wallet or keystore, key files (`.seco .ppk .pfx .p12 .pem .gpg .jks .keystore`) and app databases are never offered, and known wallet apps (Exodus, Atomic Wallet) are not listed as Program Leftovers.
- `.idb` and `.tlog` are Safe only inside build folders, and `Release`, `Debug`, `x64` and `x86` count as build folders only inside a project (`.sln`, `.vcxproj`, `CMakeLists.txt`).
- Fewer guesses by name: disc images are checked by their content (`.bin`/`.img`/`.mdf` only next to a `.cue`/`.mds`), loose `.jar` files are no longer listed, `bin`/`out`/`build`/`target` folders only next to a project file, `Package Cache` installers are kept (deleting them breaks uninstall and repair), and installed games, virtual environments and model folders are skipped by Duplicates and Stale Large Files.
- "Changed in the last 24 hours" also counts files just unpacked from an archive (the later of modified and created time).
- More Windows junk is found: Internet Explorer/Edge `INetCache`, the thumbnail cache, Windows Error Reporting archives, DirectX, NVIDIA and AMD shader caches, Chromium shader caches, Opera and Vivaldi caches, and npm, pip, NuGet and Yarn caches.
- Junk only an administrator can remove (Windows Update downloads, `Windows.old`, `C:\Windows\Temp`) is shown in a read-only **Windows cleanup that needs admin** panel that points to Windows Disk Cleanup and Storage Sense. JunkZero still doesn't delete it.

### Deleting and restoring
- Items set aside in a holding folder are erased after 7 days only when a cleanup record names them; a cleanup's full list is kept until its set-aside items are gone, even after its history entry is cleared, so nothing in a holding folder is erased without a record.
- Large deletes are much faster (items go to the Recycle Bin in batches of about 100, and drive settings are looked up once per drive), and restoring reads the Recycle Bin once instead of once per item.
- While a delete runs, the dialog shows "Deleting 1,200 of 3,000" with a progress bar and a **Cancel** button. Cancel stops after the current batch; what wasn't reached is left in place and the result says how many.
- Closing the window while a delete runs is blocked until it finishes, and only one copy of JunkZero runs at a time (opening it again brings up the running one).
- Errors no longer vanish: the error toast stays until dismissed, **Show details** lists every item with its reason and a **Copy** button, and Cleanup History keeps them.
- The Folder Explorer no longer drops `C:\foo\bar2` from the results when `C:\foo\bar` is deleted.

### Scanning
- Scan progress keeps moving on clean drives: counts update about four times a second and the current step is shown (scanning folders, checking program leftovers, looking for empty folders, comparing duplicates).
- The "scan finished" message can no longer be lost when saving "new since last scan" is slow, and an error during a scan ends it with a message instead of a spinner that runs forever. If the live connection drops, the page checks whether the scan is still running before saying it stopped.
- Duplicates are found faster: the last 64 KB is compared too before a full hash, and files are hashed in parallel. One slow folder no longer holds up the other scan workers, and archive listing is capped at about 64 MB.
- The folder picker no longer closes itself after 30 seconds.

### Scheduled scans
- On Windows, scheduled scans also run on battery and a missed time is made up when the computer is next available. The task is pointed at the current JunkZero at every start, so moving or updating the app no longer breaks it, and turning it off works on non-English Windows. The Schedule screen shows when the last scheduled scan failed.

### Windows download
- **The Windows download changes:** instead of a single `JunkZero.exe`, releases offer `JunkZero-Setup.exe` (an installer for your user account, no administrator rights needed) and `JunkZero-Windows-portable.zip` (unzip and run `JunkZero.exe` from the `JunkZero` folder). The folder build starts faster and is less often flagged by antivirus programs. Uninstalling removes the scheduled scan; settings and history are kept.
- The app has an icon and version details (Properties → Details), and each release lists SHA-256 checksums in `SHA256SUMS.txt`. The Mac app has the icon too.

### Version, updates and diagnostics
- The version is shown next to the name and in **About**. **Check for updates** asks GitHub for the latest release only when clicked, never on its own.
- JunkZero keeps a log in its data folder (`logs\junkzero.log`, rotated at 1 MB), and **Copy diagnostics** in About copies the version, system details and recent log lines for a bug report. The cleanup audit log is rotated too instead of growing forever.
- AI Inspect's Gemini option has a timeout and is never surer than the offline verdict. It works only when running from source with `google-genai` installed; the downloadable app always uses the offline analysis.

### Keyboard and screen readers
- Escape closes dialogs, Tab stays inside the open dialog and focus returns to where it was; dialogs are announced as dialogs. Column headers can be sorted from the keyboard, notifications are read out, and Enter in the path box starts a scan.

### Checks
- CI also runs a lint check (real errors only), checks the page script's syntax, and runs a browser test that scans, deletes in Permanent mode and checks the files are gone. The release build also tests the installer and uninstaller (including removing the scheduled scan) and tries a delete-and-restore with the built app.

## [1.2.0] - 2026-10-16

### Restore brings back everything
- Cleanup History kept only the first 500 items of a cleanup, so Restore could put back only those. Every item of a cleanup, and where it went, is now kept in its own file next to the history, and Restore uses all of it.
- For cleanups recorded before this, Restore also puts back everything still in that cleanup's holding folder batch (each item's place is its path inside the batch), never over something that is already there.
- A move to the holding folder that fails no longer leaves empty folders behind in it.

### Read-only and locked files
- On a Mac, FAT, exFAT, NTFS and network disks show the Windows read-only attribute as Finder's Locked flag, so old Windows files there could not be deleted ("locked"). JunkZero now clears that attribute on your own files and finishes the delete, as it does on Windows.
- Files you locked in Finder on a Mac disk are still never unlocked on their own: the message offers **Unlock and delete**, which unlocks only those items (never the folder holding them, and never system-locked files) and deletes them.

### Seeing what you delete
- The results show "Showing 500 of 7,002" whenever the list hasn't drawn every matching item, and **Show all** draws them (up to 20,000).
- **Select All** says when it ticked items further down the list than shown, and the delete dialog warns about them and asks for DELETE typed before going ahead.

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
