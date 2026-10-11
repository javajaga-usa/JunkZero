# JunkZero 🚀
> **Intelligent, High-Performance Disk Garbage Detector & Cleaner with Modern GUI**

JunkZero is a lightning-fast storage cleanup and disk optimization utility for Windows and macOS. It identifies, categorizes, and safely removes gigabytes of leftover clutter—such as APKs, software setup packages (`.exe`, `.msi`), compiled Java binaries (`.class`, `.jar`), development build caches (`node_modules`, `target/`, `__pycache__`), temporary crash logs, and broken browser downloads.

---

## ✨ Key Features

- **⚡ Blazing Fast Multi-Threaded Scanning**: Uses parallel `os.scandir` directory traversal to scan 100,000+ files in seconds without I/O freezes.
- **🖥️ Modern File Manager GUI**:
  - Live animated scan progress and metrics.
  - Storage summary cards with instant visual size breakdowns.
  - Interactive table that shows 500 rows at a time ("Show more" adds the next 500), so scans with tens of thousands of results stay responsive. Search by filename/path/extension (it runs once you pause typing) and sort by any column.
  - Quick multi-select buttons: **"Smart Select"** (only items marked Safe and Delete), **"Select All"**, **"Deselect"**.
  - **Delete Selected only removes what you can see**: ticked items hidden by a search, category, folder or "New only" filter are left alone, and the footer says how many of those there are.
  - **Open Enclosing Folder**: One-click jump directly to the file in Windows Explorer or Finder. On a Mac, apps and packages (`.app`, Photos libraries) are shown in Finder, never opened.
- **🎯 Accurate Garbage Classification**:
  - **Installers & Setup Packages**: `.msi`, `.apk`, `.iso`, `.dmg`, `.pkg`, and `.exe` files that are setup programs. JunkZero reads a few MB of each `.exe` and lists it only when it finds an installer builder's fingerprint (Inno Setup, NSIS, InstallShield, WiX, Squirrel, Advanced Installer and others) or version info that calls it a setup or installer. Portable apps, tools, games, uninstallers and programs inside an installed or unpacked app folder are never listed. An `.exe` in Downloads, Desktop or temp that is only named like a setup file is listed as Review with a lower score.
  - **Setup Archives**: a `.zip` or `.tar.gz` is listed only when JunkZero looks inside (without unpacking) and finds a setup program, installer package, disk image or portable app with nothing personal alongside. Archives with documents, photos, videos or other personal files are never listed, whatever their name, and neither are password-protected or unreadable ones (including `.rar` and `.7z`, which JunkZero can't look inside). The Smart Score reasons say what was found inside.
  - **Old Java & Build Artifacts**: Compiled `.class` files, standalone `.jar`s, `node_modules/`, `target/`, `__pycache__/`, `.obj`, `.pyc`. Compiled code (`.class`, `.pyc`) and file types that are also used for real files (`.obj` 3D models, `.pdb` protein data, `.user`, `.orig`) are only marked Safe inside a build output folder (`bin`, `obj`, `build`, `Debug`...) or `__pycache__`; elsewhere they are Review Recommended. A build folder that holds its own `.git` is never deleted, and one with personal files inside asks you to type DELETE.
  - **Temporary & Cache Files**: `.tmp`, `.log` and `.dmp` files inside temp, cache, log and build folders, `thumbs.db`, crash dumps. Backup copies (`.bak`, `.old`) are listed as Review Recommended and never preselected.
  - **Broken Downloads**: Incomplete `.crdownload`, `.part`, `.download` files.
  - **Stale Large Files**: Files ≥ 100MB unmodified for ≥ 180 days.
  - **Empty Folders**: Folders with no files at any depth (only the topmost folder of an empty tree is listed). Empty folders are never preselected, since some apps expect their folders to exist. Before deleting, JunkZero re-checks the folder and skips it if files have appeared since the scan. Version-control folders (`.git`, `.svn`, `.hg`) are never scanned or deleted from, because Git needs some of its empty folders.
  - **Duplicate Files** (opt-in): Files ≥ 1MB with identical contents. The copy in Documents, Pictures, Desktop or a cloud folder is kept over one in Downloads or temp (otherwise the newest); the others are listed for review. A copy is deleted only while another identical copy is still there, so the last one is never removed. Hard links are never counted as copies.
  - **Old Downloads** (opt-in): Files in your Downloads folder that haven't been modified or added for 90 days or more. They are listed as "Review Recommended" and never preselected. Files that fit another category (installers, broken downloads) stay in that category.
- **📝 Junk Rules**: Add your own patterns (such as `*.bak2` or `render_*`) or folders to flag as junk. Matches appear under "My Junk Rules" as Review Recommended and are never preselected; a matching folder is listed as one item. Built-in categories and exclusions take priority, rules that would match everything (`*`, `*.*`, a whole drive) are refused, and Quick Clean ignores your rules.
- **✨ New Since Last Scan**: When you scan a folder again, items the previous scan of that folder didn't find get a "New" badge, and a "New only" button shows just those. Only categories the previous scan also looked for are compared, so turning on a category doesn't mark everything in it as new. The last 20 scanned folders are remembered.
- **📂 Junk by Folder**: Groups the current results by the folder they are in, biggest first. Click a folder to show only its items in the table; clear the folder chip to see everything again.
- **🧮 Smart Score**: Every result gets a 1-99 score for how sure JunkZero is that it's junk, with a Delete / Review / Keep label in its own column. Click a score to see why: each rule that moved it is listed (its risk level, category, age, whether it sits in Downloads or Desktop, and what you did with similar items before). **Smart Select** ticks only the items marked Delete. Only items rated Safe can be marked Delete, however much was learned, and an item is preselected only when it is also marked Delete. Scoring runs entirely offline.
- **📈 Learns From Your Choices**: When you delete items from the results, JunkZero remembers their kind (category and file type), and similar items score higher next time. Items you leave alone and that turn up again in a later scan count as kept (at most once a day) and score lower. Cleanup History shows how much has been learned, with a button to forget it.
- **🧩 Program Leftovers** (opt-in): Finds folders in `AppData\Roaming` and `AppData\Local` (on a Mac, `~/Library/Application Support`) left behind by programs you've uninstalled. A folder is listed only if its name matches nothing in the Windows list of installed programs or in Program Files (on a Mac: the apps in Applications, their bundle ids, background services and Homebrew packages), it isn't a shared folder (Microsoft, Packages, Temp, npm...), and nothing inside it has changed for 180 days. Folders holding personal files or wallets are left out. Leftovers are Review Recommended and never preselected; if the installed-programs list can't be read, or a folder is too big to check fully, nothing is reported.
- **💬 Scan Summary**: After a scan, a short summary says how much can go now with little risk, what's worth a look first (naming the biggest item), and what looks worth keeping, with a button to select the safe bets.
- **📏 Largest Files & Folders**: A read-only view of the biggest folders and files in the target folder, so you can see where the space goes even when it isn't junk. Review or delete through the folder explorer, which keeps the usual confirmations. Protected system folders and your exclusions are not counted.
- **💾 Remembered Settings**: The target folder, the "Scan For" toggles and the theme are restored the next time you open JunkZero. The delete mode is never remembered; every launch starts in Recycle Bin mode.
- **⚡ Quick Clean**: One click scans your temp folder, application crash dumps, and Chrome / Edge / Brave / Firefox caches. Browser cache folders are listed as single items (close the browser before deleting them). Locations under `C:\Windows` (Windows Update downloads, system temp) stay protected and are not offered.
- **🙈 Exclusions**: Mark a file or folder as "never flag" from its row, or add paths and patterns such as `*.iso` in the Exclusions dialog. Excluded folders are not scanned at all.
- **🕘 Cleanup History**: Every cleanup is logged with its date, delete mode, item list and space freed. **Restore** puts a cleanup's items back from the Recycle Bin, Trash or holding folder, never over something that is already there; on a Mac it restores only the very item JunkZero moved to the Trash.
- **📊 Space Breakdown**: A bar under the summary cards shows where the reclaimable space is by category; click a segment to filter the table.
- **📅 Scheduled Scans (report only)**: Schedule daily or weekly scans through Windows Task Scheduler (launchd on a Mac). Scheduled scans never delete anything; they save a report you can review in the table later. Run one manually with `python -m app.main --mode report --path C:\Users\You\Downloads`.
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
  - Works 100% offline with built-in heuristic database. Only if you set `GEMINI_API_KEY` yourself does it send the file's name, path and size (never its contents) to Google Gemini for deeper insights.

## 🍎 On a Mac

JunkZero runs on macOS 11 or later with the same screens, and swaps in the Mac's own places and names:

- **Quick Clean** looks at each app's folder in `~/Library/Caches` (one item per app; the caches macOS keeps for itself, `com.apple.*`, are left alone), log files and crash reports in `~/Library/Logs`, your temp folder, Xcode's DerivedData, iOS Simulator caches and the npm cache.
- **Protected**: `/System`, `/Library`, `/Applications`, `/usr`, `/private` and the other system folders are never scanned or deleted from. Inside `~/Library`, only Caches, Logs, Application Support and Xcode's build data are looked at, so Mail, Messages, Keychains, iCloud Drive and app containers are never touched, and neither are iPhone backups (`MobileSync`). What's inside apps and Photos or Music libraries isn't scanned on its own, and the Trash, Spotlight and other disk metadata folders are skipped. Your home folder, Library, Desktop, Documents, Downloads, Movies, Music and Pictures can't be deleted as a whole.
- **Drives** are the startup disk (`/`) and any external disks in `/Volumes`.
- Deleted items go to the **Trash**, the folder buttons open **Finder**, Browse uses the Finder folder picker, and **scheduled scans** run through launchd (a job in `~/Library/LaunchAgents`).
- Settings and history are kept in `~/Library/Application Support/JunkZero`.

---

## 🚀 Quick Start

### Download (no Python needed)
Get `JunkZero.exe` from the [latest release](https://github.com/javajaga-usa/JunkZero/releases/latest) and double-click it. It needs Windows 10 or 11 with the Microsoft Edge WebView2 runtime, which Windows normally includes. The exe isn't code-signed yet, so Windows SmartScreen may warn the first time: choose **More info → Run anyway**. See [CHANGELOG.md](CHANGELOG.md) for what's in each release.

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

CI runs the test suite on Windows, macOS and Linux with Python 3.10, 3.12 (which builds the releases) and 3.13, checks dependencies, compiles Python sources, and verifies JavaScript syntax and HTML escaping. Test dependencies omit the optional native GUI runtime.

To run the API without opening a window:
```powershell
python -m app.main --mode server
```
It prints the address to open, which carries this launch's app token.

Settings (exclusions, junk rules, schedule, remembered scan options), cleanup history, the latest scan report and the results of recent scans (for "New since last scan") are stored in `%APPDATA%\JunkZero` on Windows, `~/Library/Application Support/JunkZero` on a Mac (or `~/.junkzero` elsewhere; override with `JUNKZERO_DATA_DIR`).

### Building the exe
```powershell
python -m pip install -r requirements.txt -c packaging/release-constraints.txt pyinstaller
pyinstaller --noconfirm packaging/JunkZero.spec   # writes dist\JunkZero.exe
```
On a Mac the same command writes `dist/JunkZero.app`. The Release workflow builds and smoke-tests the exe and the Mac app on every pull request, and attaches both (`JunkZero.exe` and `JunkZero-macOS.zip`) to the release. Release builds use the exact versions in `packaging/release-constraints.txt`. To publish a release, set `__version__` in `app/__init__.py` to a version not released yet, add a matching section to `CHANGELOG.md`, merge to main, and run the Release workflow on main by hand (or push a `v<version>` tag on a commit that is on main); the files are attached to the GitHub release. Runs from other branches build but never publish.

Use the application on localhost. Native folder selection and Explorer / Finder integration require Windows or macOS. Scan results are heuristic suggestions; review them before deletion. Folder inspector sizes are depth-limited estimates.
