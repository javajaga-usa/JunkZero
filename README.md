# JunkZero 🚀
> **Intelligent, High-Performance Disk Garbage Detector & Cleaner with Modern GUI**

JunkZero is a lightning-fast storage cleanup and disk optimization utility designed for Windows. It identifies, categorizes, and safely removes gigabytes of leftover clutter—such as APKs, software setup packages (`.exe`, `.msi`), compiled Java binaries (`.class`, `.jar`), development build caches (`node_modules`, `target/`, `__pycache__`), temporary crash logs, and broken browser downloads.

---

## ✨ Key Features

- **⚡ Blazing Fast Multi-Threaded Scanning**: Uses parallel `os.scandir` directory traversal to scan 100,000+ files in seconds without I/O freezes.
- **🖥️ Modern File Manager GUI**:
  - Live animated scan progress and metrics.
  - Storage summary cards with instant visual size breakdowns.
  - Interactive table with virtualized scrolling, search by filename/path/extension, and column sorting.
  - Quick multi-select buttons: **"Select All Safe"**, **"Select All"**, **"Deselect"**.
  - **Open Enclosing Folder**: One-click jump directly to the file in Windows Explorer.
- **🎯 Accurate Garbage Classification**:
  - **Installers & Setup Packages**: `.exe`, `.msi`, `.apk`, `.iso`, `.dmg`, `.pkg`.
  - **Old Java & Build Artifacts**: Compiled `.class` files, standalone `.jar`s, `node_modules/`, `target/`, `__pycache__/`, `.obj`, `.pyc`.
  - **Temporary & Cache Files**: `.tmp`, `.log`, `.dmp`, `thumbs.db`, crash dumps.
  - **Broken Downloads**: Incomplete `.crdownload`, `.part`, `.download` files.
  - **Stale Large Files**: Files ≥ 100MB unmodified for ≥ 180 days.
  - **Empty Folders**: Folders with no files at any depth (only the topmost folder of an empty tree is listed). Before deleting, JunkZero re-checks the folder and skips it if files have appeared since the scan.
- **🛡️ Ironclad Safety Safeguards**:
  - **Recycle Bin Support (`send2trash`)**: API and engine calls default to the Recycle Bin. The current GUI explicitly requests permanent deletion and displays a confirmation dialog.
  - **OS System Protection**: Core directories (`C:\Windows`, `System32`, `Program Files`, `$Recycle.Bin`) are strictly protected from accidental deletion.
  - **Permanent Deletion Confirmation**: The GUI requires confirmation before permanently deleting selected files or folders.
  - **Permanent Mode (opt-in)**: Switch the footer toggle from *Recycle Bin* to *Permanent* to erase files directly. Permanent deletes need an extra "cannot be restored" confirmation, and the app always starts back in Recycle Bin mode.
- **🧠 AI Inspector (Gemini Integration)**:
  - Click the **AI Inspect** robot icon on any file to receive an instant analysis of its origin application, purpose, and safety verdict.
  - Works 100% offline with built-in heuristic database, or connects with `GEMINI_API_KEY` for deep contextual insights.

---

## 🚀 Quick Start

### 1. Requirements
- Windows 10 / 11
- Python 3.10+ (Tested on Python 3.13)

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

CI runs the test suite on Windows and Linux with Python 3.10 and 3.13, checks dependencies, compiles Python sources, and verifies JavaScript syntax and HTML escaping. Test dependencies omit the optional native GUI runtime.

To run the API without opening a window:
```powershell
python -m app.main --mode server
```

Use the application on localhost. Native folder selection and Explorer integration require Windows. Scan results are heuristic suggestions; review them before deletion. Folder inspector sizes are depth-limited estimates.
