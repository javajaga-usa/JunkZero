# JunkZero 🚀
> **Intelligent, High-Performance Disk Garbage Detector & Cleaner with Modern GUI**

JunkZero is a lightning-fast storage cleanup and disk optimization utility for Windows and macOS. It identifies, categorizes, and safely removes gigabytes of leftover clutter—such as APKs, software setup packages (`.exe`, `.msi`), compiled Java binaries (`.class`, `.jar`), development build caches (`node_modules`, `target/`, `__pycache__`), temporary crash logs, and broken browser downloads.

---

## ✨ Key Features

- **⚡ Blazing Fast Multi-Threaded Scanning**: Uses parallel `os.scandir` directory traversal to scan 100,000+ files in seconds without I/O freezes.
- **🖥️ Modern File Manager GUI**:
  - Scan progress: files and folders checked so far, updated a few times a second, and the current step (scanning folders, checking program leftovers, looking for empty folders, comparing duplicates). The scan can't know in advance how much there is, so there is no percentage. Found items appear in the table while the scan runs.
  - Delete progress: a large cleanup shows "Deleting 1,200 of 3,000" with a bar and a **Cancel** button, which stops after the current batch and leaves the rest in place.
  - Problems stay on screen: a toast such as "3 items couldn't be removed" stays until you dismiss it, and **Show details** lists every path with its reason (with **Copy**). Cleanup History keeps them too.
  - Keyboard: Enter in the path box starts a scan, Escape closes dialogs, and the column headers can be sorted from the keyboard.
  - Storage summary cards with instant visual size breakdowns.
  - Interactive table that shows 500 rows at a time ("Show more" adds the next 500), so scans with tens of thousands of results stay responsive. Search by filename/path/extension (it runs once you pause typing) and sort by any column.
  - Quick multi-select buttons: **"Smart Select"** (only items marked Safe and Delete), **"Select All"**, **"Deselect"**.
  - **Delete Selected only removes what you can see**: ticked items hidden by a search, category, folder or "New only" filter are left alone, and the footer says how many of those there are.
  - **Open Enclosing Folder**: One-click jump directly to the file in Windows Explorer or Finder. On a Mac, apps and packages (`.app`, Photos libraries) are shown in Finder, never opened.
- **🎯 Accurate Garbage Classification**:
  - **Installers & Setup Packages**: `.msi`, `.apk`, `.iso`, `.dmg`, `.pkg`, and `.exe` files that are setup programs. JunkZero reads a few MB of each `.exe` and lists it only when it finds an installer builder's fingerprint (Inno Setup, NSIS, InstallShield, WiX, Squirrel, Advanced Installer and others) or version info that calls it a setup or installer. Portable apps, tools, games, uninstallers and programs inside an installed or unpacked app folder are never listed. An `.exe` in Downloads, Desktop or temp that is only named like a setup file is listed as Review with a lower score. On Windows, a setup program for something already installed at the same or a newer version scores higher, and says so ("7-Zip 23.01 (x64) is already installed"); the names must match exactly once versions and "(x64)" are left out.
  - **Setup Archives**: a `.zip` or `.tar.gz` is listed only when JunkZero looks inside (without unpacking) and finds a setup program, installer package, disk image or portable app with nothing personal alongside. Archives with documents, photos, videos or other personal files are never listed, whatever their name, and neither are password-protected or unreadable ones (including `.rar` and `.7z`, which JunkZero can't look inside). The Smart Score reasons say what was found inside.
  - **Old Java & Build Artifacts**: Compiled `.class` files, standalone `.jar`s, `node_modules/`, `target/`, `__pycache__/`, `.obj`, `.pyc`. Compiled code (`.class`, `.pyc`) and file types that are also used for real files (`.obj` 3D models, `.pdb` protein data, `.user`, `.orig`) are only marked Safe inside a build output folder (`bin`, `obj`, `build`, `Debug`...) or `__pycache__`; elsewhere they are Review Recommended. A build folder that holds its own `.git` is never deleted, and one with personal files inside asks you to type DELETE.
  - **Temporary & Cache Files**: `.tmp`, `.log` and `.dmp` files inside temp, cache, log and build folders, `thumbs.db`, crash dumps. Backup copies (`.bak`, `.old`) are listed as Review Recommended and never preselected.
  - **Broken Downloads**: Incomplete `.crdownload`, `.part`, `.download` files.
  - **Stale Large Files**: Files ≥ 100MB unmodified for ≥ 180 days.
  - **Empty Folders**: Folders with no files at any depth (only the topmost folder of an empty tree is listed). Empty folders are never preselected, since some apps expect their folders to exist. Before deleting, JunkZero re-checks the folder and skips it if files have appeared since the scan. Version-control folders (`.git`, `.svn`, `.hg`) are never scanned or deleted from, because Git needs some of its empty folders.
  - **Duplicate Files** (opt-in): Files ≥ 1MB with identical contents. The copy in Documents, Pictures, Desktop or a cloud folder is kept over one in Downloads or temp (otherwise the newest); the others are listed for review. A copy is deleted only while another identical copy is still there, so the last one is never removed. Hard links are never counted as copies.
  - **Duplicate Folders** (with Duplicates): whole folders holding exactly the same files, such as a photo folder copied twice. A folder is listed only when the scan saw everything in it, it holds at least two files and 1 MB, and every file matches the file at the same place in the other copy byte for byte. Only the outermost copy is listed (not each of its subfolders), the copy in Documents, Pictures or a cloud folder is kept, and both copies are compared again right before deleting. Review Recommended, never preselected.
  - **Old Coding Projects** (opt-in): `node_modules`, Python virtual environments (`.venv`), Rust and Maven `target`, Gradle and CMake `build`, and .NET `bin`/`obj` folders of projects nothing changed in for 90 days. A folder is listed only when the project file that rebuilds it sits right next to it (`package.json`, `Cargo.toml`, `pom.xml`, a `.csproj`...), and the reason says how to get it back (for example "npm install brings it back"). The project's own files and its Git index count as activity; a project too big to check is not listed. Review Recommended, never preselected.
  - **Old Downloads** (opt-in): Files in your Downloads folder that haven't been modified or added for 90 days or more. They are listed as "Review Recommended" and never preselected. Files that fit another category (installers, broken downloads) stay in that category.
- **🔎 Search by File Type**: Open **File Types**, pick ready-made groups (videos, photos, music, documents, archives, disk images, installers, logs and temp, developer output) or type your own extensions (`.mp4`, `tar.gz`), optionally only files unchanged for N days, and click **Search these types**. The search replaces the current results; matching files in the scan location are listed under "Chosen File Types" in the deletion list as Review Recommended and are never preselected: tick the ones you want and delete them like any other result, with the same exclusions, protected folders, personal-file and cloud safeguards. Tick **Chosen file types** under *Look for* to include them in every deep scan too. Backup images and virtual machine disks are never listed.
- **📝 Junk Rules**: Add your own patterns (such as `*.bak2` or `render_*`) or folders to flag as junk. Matches appear under "My Junk Rules" as Review Recommended and are never preselected; a matching folder is listed as one item. Built-in categories and exclusions take priority, rules that would match everything (`*`, `*.*`, a whole drive) are refused, and Quick Clean ignores your rules.
- **✨ New Since Last Scan**: When you scan a folder again, items the previous scan of that folder didn't find get a "New" badge, and a "New only" button shows just those. Only categories the previous scan also looked for are compared, so turning on a category doesn't mark everything in it as new. The last 20 scanned folders are remembered.
- **📂 Junk by Folder**: Groups the current results by the folder they are in, biggest first. Click a folder to show only its items in the table; clear the folder chip to see everything again.
- **🧮 Smart Score**: Every result gets a 1-99 score for how sure JunkZero is that it's junk, with a Delete / Review / Keep label in its own column. Click a score to see why: each rule that moved it is listed (its risk level, category, age, whether it sits in Downloads or Desktop, and what you did with similar items before). **Smart Select** ticks only the items marked Delete. Only items rated Safe can be marked Delete, however much was learned, and an item is preselected only when it is also marked Delete. Scoring runs entirely offline.
- **📈 Learns From Your Choices**: When you delete items from the results, JunkZero remembers their kind (category and file type), and similar items score higher next time. Items you leave alone and that turn up again in a later scan count as kept (at most once a day) and score lower. Cleanup History shows how much has been learned, with a button to forget it.
- **🧩 Program Leftovers** (opt-in): Finds folders in `AppData\Roaming` and `AppData\Local` (on a Mac, `~/Library/Application Support`) left behind by programs you've uninstalled. A folder is listed only if its name matches nothing in the Windows list of installed programs or in Program Files (on a Mac: the apps in Applications, their bundle ids, background services and Homebrew packages), it isn't a shared folder (Microsoft, Packages, Temp, npm...), and nothing inside it has changed for 180 days. Folders holding personal files or wallets are left out. Leftovers are Review Recommended and never preselected; if the installed-programs list can't be read, or a folder is too big to check fully, nothing is reported.
- **💬 Scan Summary**: After a scan, a short summary says how much can go now with little risk, what's worth a look first (naming the biggest item), and what looks worth keeping, with a button to select the safe bets.
- **📏 Largest Files & Folders**: A read-only view of the biggest folders and files in the target folder, so you can see where the space goes even when it isn't junk. The **Map** tab draws the folders as boxes sized by the space they take; click a box to look inside that folder, and the path above the map to go back. Review or delete through the folder explorer, which keeps the usual confirmations. Protected system folders and your exclusions are not counted.
- **💾 Remembered Settings**: The target folder, the "Scan For" toggles and the theme are restored the next time you open JunkZero. The delete mode is never remembered; every launch starts in Recycle Bin mode.
- **⚡ Quick Clean**: One click scans your temp folder, application crash dumps, and Chrome / Edge / Brave / Firefox caches. Browser cache folders are listed as single items (close the browser before deleting them). The caches of Microsoft Teams, Slack, Discord, Zoom (its logs), VS Code and Steam are offered too, from an exact list of their cache folders, and only while that app is closed; if JunkZero can't tell which programs are running, none of them is offered. Their messages, settings and downloads are never touched. Locations under `C:\Windows` (Windows Update downloads, system temp) and `Windows.old` stay protected and are not offered; when they hold something, a **Windows cleanup that needs admin** panel lists them with their size and points to Windows Disk Cleanup or Storage Sense.
- **ℹ️ About**: The version is shown next to the name. **About → Check for updates** asks GitHub for the latest release only when you click it; **Copy diagnostics** copies the version, system details and recent log lines for a bug report.
- **🙈 Exclusions**: Tick **Exclude** in a result's row (before its category), or add paths and patterns such as `*.iso` in the **Exclusions** dialog at the top. An excluded item is greyed out and can't be ticked, is never deleted from any screen (and neither is a folder holding one), and isn't listed by later scans until you remove it from Exclusions. Untick Exclude on the row to undo. Excluded folders are not scanned at all.
- **💽 Deletes that fit the disk**: Before deleting, the confirm dialog names each disk, its format and what will happen there: the Recycle Bin / Trash, JunkZero's hidden holding folder on that disk for 7 days (USB drives, memory cards, network drives, exFAT/FAT/NTFS disks on a Mac, items too big for a Windows Recycle Bin), or nothing because the disk is read-only (CDs, NTFS on a Mac). A note under the scan location says the same when you pick such a disk.
- **🕘 Cleanup History**: Every cleanup is logged with its date, delete mode, item list, space freed and what couldn't be removed. **Restore** puts a cleanup's items back from the Recycle Bin, Trash or holding folder, never over something that is already there; on a Mac it restores only the very item JunkZero moved to the Trash. Restore works only while the items are still there: not after the Recycle Bin or Trash was emptied, not for items Windows erased instead of keeping, and never for Permanent deletes. Items set aside on drives without a Recycle Bin are kept for 7 days.
  - **Space still held**: deleting to the Recycle Bin frees no space until it is emptied. The top of Cleanup History says how much of what you deleted with JunkZero is still in the Recycle Bin / Trash and the holding folders, and **Empty** erases only JunkZero's own items from cleanups at least N days old (7 by default). Only items JunkZero can point to exactly are counted: on Windows the Recycle Bin entry with the same original path and deletion time, on a Mac the very item it moved (by its file id). Everything else in the Recycle Bin or Trash is left alone. Emptied items can't be restored.
  - **Free space over time**: a chart of each disk's free space at every scan and cleanup, with a line for each cleanup.
- **📊 Space Breakdown**: A bar under the summary cards shows where the reclaimable space is by category; click a segment to filter the table.
- **📅 Scheduled Scans (report only)**: Schedule daily or weekly scans through Windows Task Scheduler (launchd on a Mac). Scheduled scans never delete anything; they save a report you can review in the table later. They run only while you are signed in; a scan missed while the computer was off runs when it is next available, and the Schedule screen shows when the last one failed. Uninstalling with the installer removes the scheduled scan. A scheduled scan that finds at least 500 MB (you can change the amount, or turn it off, in Schedule) shows a notification. On Windows, clicking it opens JunkZero with the report in the table; Windows shows it under the name Windows PowerShell, which sends it for JunkZero. On a Mac and Linux the notification says what was found. Whatever the system, the next time you open JunkZero it offers a report it hasn't shown yet. Run one manually with `python -m app.main --mode report --path C:\Users\You\Downloads`.
- **📄 CSV Export**: Exports the rows currently shown. Cells are escaped so file names can't run as spreadsheet formulas.
- **🛡️ Ironclad Safety Safeguards**:
  - **Recycle Bin by Default (`send2trash`)**: The GUI, API and engine all move deleted items to the Recycle Bin unless Permanent mode is chosen.
  - **OS System Protection**: Core directories (`C:\Windows`, `System32`, `Program Files`, `$Recycle.Bin`) are strictly protected from accidental deletion.
  - **Personal Folder Protection**: Your user profile, other profiles in `C:\Users`, and personal folders such as Desktop, Documents, Downloads and Pictures can't be deleted as a whole (what's inside them still can).
  - **Local Only**: The app's server only answers requests addressed to `127.0.0.1`/`localhost`, and uses a free port if 8000 is taken. Icons are bundled, so the app works fully offline.
  - **App Token**: Every request to the app's server needs a secret made fresh at each launch, which only JunkZero's own window gets (in its address, never in the page). Other web pages, other programs and other user accounts on the computer can't read your scan or delete anything. Only items from the latest scan can be deleted from the results.
  - **Recycle Bin that really keeps things**: USB sticks, memory cards and network drives have no Recycle Bin, and Windows erases items too big for the Recycle Bin (or everything, when it is set to remove files immediately). Such items go to a hidden `.JunkZero-holding` folder on the same drive for 7 days instead, and can be restored from Cleanup History.
  - **Personal files**: documents, photos, videos, wallets, game saves, cloud-synced files and anything changed in the last 24 hours are never preselected or marked Delete. Backup folders, backup images and virtual machine disks are never listed. Files open in another program are skipped, and if JunkZero can't tell which files are open, nothing is deleted.
  - **Typed confirmation**: deletes that include personal files, more than 5 GB or more than 1,000 items need DELETE typed, and the dialog shows what will be removed first.
  - **Permanent Deletion Confirmation**: The GUI requires confirmation before permanently deleting selected files or folders.
  - **Permanent Mode (opt-in)**: Switch the footer toggle from *Recycle Bin* to *Permanent* to erase files directly. Permanent deletes need an extra "cannot be restored" confirmation, and the app always starts back in Recycle Bin mode.
- **🧠 AI Inspector (Gemini Integration)**:
  - Click the **AI Inspect** robot icon on any file to receive an instant analysis of its origin application, purpose, and safety verdict. It is never surer than the scanner: build folders and disk images found by name are Review.
  - Works 100% offline with built-in heuristic database. The Gemini option works only when running from source: install `google-genai` (`python -m pip install google-genai`) and set `GEMINI_API_KEY`; it then sends the file's name, path and size (never its contents) to Google Gemini. The downloadable app doesn't include the Gemini library, so it always uses the offline analysis.

## 🍎 On a Mac

JunkZero runs on macOS 11 or later with the same screens, and swaps in the Mac's own places and names:

- **Quick Clean** looks at each app's folder in `~/Library/Caches` (one item per app; the caches macOS keeps for itself, `com.apple.*`, are left alone), log files and crash reports in `~/Library/Logs`, your temp folder, Xcode's DerivedData, iOS Simulator caches and the npm cache.
- **Protected**: `/System`, `/Library`, `/Applications`, `/usr`, `/private` and the other system folders are never scanned or deleted from. Inside `~/Library`, only Caches, Logs, Application Support and Xcode's build data are looked at, so Mail, Messages, Keychains, iCloud Drive and app containers are never touched, and neither are iPhone backups (`MobileSync`). What's inside apps and Photos or Music libraries isn't scanned on its own, and the Trash, Spotlight and other disk metadata folders are skipped. Your home folder, Library, Desktop, Documents, Downloads, Movies, Music and Pictures can't be deleted as a whole.
- **Drives** are the startup disk (`/`) and any external disks in `/Volumes`.
- Deleted items go to the **Trash** on the startup disk and APFS or Mac OS Extended disks. On exFAT, FAT, NTFS and network disks, where macOS may erase an item instead of keeping it in the Trash, they go to a hidden `.JunkZero-holding` folder on that disk for 7 days (restore them from Cleanup History). The folder buttons open **Finder**, Browse uses the Finder folder picker, and **scheduled scans** run through launchd (a job in `~/Library/LaunchAgents`).
- Settings and history are kept in `~/Library/Application Support/JunkZero`.
- **Full Disk Access**: without it, macOS keeps apps out of Desktop, Documents, Downloads, external disks and more. If a scan or delete runs into that, JunkZero says so and its **Open Privacy & Security** button opens the Full Disk Access list; add JunkZero (the app) or Terminal (if you start it with `run.command`), then restart JunkZero.
- **From source**, start JunkZero with `run.command`: it creates `.venv` on the first run and installs `requirements.txt` whenever it changes. The Trash needs `pyobjc-framework-Cocoa` (in `requirements.txt`); without it macOS refuses Trash moves with "Insufficient access privileges for operation", and JunkZero shows how to fix it when it starts.

---

## 🚀 Quick Start

### Download (no Python needed)
From the [latest release](https://github.com/javajaga-usa/JunkZero/releases/latest), get either:
- `JunkZero-Setup.exe`: installs JunkZero for your user account (no administrator rights needed) with a Start menu entry. Uninstall it from Settings → Apps; that also removes a scheduled scan. Your settings and history in `%APPDATA%\JunkZero` are kept.
- `JunkZero-Windows-portable.zip`: unzip it anywhere and run `JunkZero.exe` inside the `JunkZero` folder (keep the folder's other files next to it).

Releases up to 1.2.0 were a single `JunkZero.exe`; from the next release the app comes as the installer or the portable folder instead, which starts faster. `SHA256SUMS.txt` in each release lists the SHA-256 checksum of every download (check one with `Get-FileHash JunkZero-Setup.exe`). JunkZero needs Windows 10 or 11 with the Microsoft Edge WebView2 runtime, which Windows normally includes. It isn't code-signed yet, so Windows SmartScreen may warn the first time: choose **More info → Run anyway**. See [CHANGELOG.md](CHANGELOG.md) for what's in each release.

On a Mac, download `JunkZero-macOS.zip` from the same release, unzip it and move `JunkZero.app` to Applications. The app isn't signed by Apple yet, so the first time macOS says it can't check it: open **System Settings → Privacy & Security** and choose **Open Anyway** (on older macOS, right-click the app and choose **Open**). The download is built for Apple silicon (M1 and later) Macs.

### Run from source

### 1. Requirements
- Windows 10 / 11, or macOS 11 or later
- Python 3.10+ (tested on 3.10, 3.12 and 3.13)

### 2. Install Dependencies
```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
```

### 3. Launching the App
Activate the virtual environment before running terminal commands:
```powershell
.venv\Scripts\activate
```
The launcher automatically uses `.venv` when present. Simply double-click:
```powershell
run.bat
```
On a Mac, set up the virtual environment with `python3 -m venv .venv && .venv/bin/python -m pip install -r requirements.txt`, then double-click `run.command` (or run `./run.command`).

Or run from terminal:
```powershell
# Launch Native Desktop Window (powered by Edge WebView2):
python -m app.main --mode gui

# Or launch in your default web browser:
python -m app.main --mode browser
```

---

## 🧪 Running Tests
```powershell
python -m pip install -r requirements-test.txt
python -m pytest -q
```

CI runs the test suite on Windows, macOS and Linux with Python 3.10, 3.12 (which builds the releases) and 3.13, checks dependencies, compiles Python sources, and verifies JavaScript syntax and HTML escaping. Test dependencies omit the optional native GUI runtime. A lint job runs `ruff check .` (real errors only, see `ruff.toml`), and a browser job runs `tests/test_browser.py` in Chromium: it starts JunkZero in server mode, scans a folder with junk, deletes it permanently from the page and checks the files are gone. To run it locally: `python -m pip install playwright` and `python -m playwright install chromium`, then `python -m pytest tests/test_browser.py` (it is skipped when Playwright isn't installed).

To run the API without opening a window:
```powershell
python -m app.main --mode server
```
It prints the address to open, which carries this launch's app token.

Settings (exclusions, junk rules, schedule, remembered scan options), cleanup history, the latest scan report and the results of recent scans (for "New since last scan") are stored in `%APPDATA%\JunkZero` on Windows, `~/Library/Application Support/JunkZero` on a Mac (or `~/.junkzero` elsewhere; override with `JUNKZERO_DATA_DIR`).

### Building the exe
```powershell
python -m pip install -r requirements.txt -c packaging/release-constraints.txt pyinstaller
pyinstaller --noconfirm packaging/JunkZero.spec   # writes dist\JunkZero\JunkZero.exe and its files
& "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" /DAppVersion=1.2.0 packaging\JunkZero.iss   # optional: dist\JunkZero-Setup.exe
```
On a Mac the same PyInstaller command writes `dist/JunkZero.app`. The icons in `packaging/` are drawn by `packaging/make_icon.py` (needs Pillow). The Release workflow builds and smoke-tests the Windows build, its installer and uninstaller, and the Mac app on every pull request, and attaches `JunkZero-Setup.exe`, `JunkZero-Windows-portable.zip`, `JunkZero-macOS.zip` and `SHA256SUMS.txt` to the release. Each CI run also offers the Windows folder build as the `JunkZero-exe` artifact for testing. Release builds use the exact versions in `packaging/release-constraints.txt`. To publish a release, set `__version__` in `app/__init__.py` to a version not released yet, add a matching section to `CHANGELOG.md`, merge to main, and run the Release workflow on main by hand (or push a `v<version>` tag on a commit that is on main); the files are attached to the GitHub release. Runs from other branches build but never publish.

Use the application on localhost. Native folder selection and Explorer / Finder integration require Windows or macOS. Scan results are heuristic suggestions; review them before deletion. Folder inspector sizes are depth-limited estimates.
