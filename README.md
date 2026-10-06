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
  - **Stale Large Files**: Files > 100MB unaccessed for > 180 days.
  - **Empty Folders**: Folders with no files at any depth (only the topmost folder of an empty tree is listed). Before deleting, JunkZero re-checks the folder and skips it if files have appeared since the scan.
- **🛡️ Ironclad Safety Safeguards**:
  - **Windows Recycle Bin by Default (`send2trash`)**: Files are safely sent to the Recycle Bin so they can be restored at any time.
  - **OS System Protection**: Core directories (`C:\Windows`, `System32`, `Program Files`, `$Recycle.Bin`) are strictly protected from accidental deletion.
  - **Permanent Mode (opt-in)**: Switch the footer toggle from *Recycle Bin* to *Permanent* to erase files directly. Permanent deletes need an extra "cannot be restored" confirmation, and the app always starts back in Recycle Bin mode.
- **🧠 AI Inspector (Gemini Integration)**:
  - Click the **AI Inspect** robot icon on any file to receive an instant analysis of its origin application, purpose, and safety verdict.
  - Works 100% offline with built-in heuristic database, or connects with `GEMINI_API_KEY` for deep contextual insights.

---

## 🚀 Quick Start

### 1. Requirements
- Windows 10 / 11
- Python 3.10+ (Tested on Python 3.13)

### 2. Launching the App
Simply double-click:
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
python -m pytest tests/
```
