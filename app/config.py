"""Configuration settings and garbage classification rules for JunkZero."""
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

# Category Constants
CAT_INSTALLERS = "Installers, Setup Archives & OS Images"
CAT_JAVA_BUILDS = "Old Java & Build Artifacts"
CAT_TEMP_JUNK = "Temporary & Cache Files"
CAT_BROKEN_DOWNLOADS = "Broken / Incomplete Downloads"
CAT_STALE_LARGE = "Stale Large Files"
CAT_EMPTY_FOLDERS = "Empty Folders"
CAT_DUPLICATES = "Duplicate Files"
CAT_OLD_DOWNLOADS = "Old Downloads"
CAT_CUSTOM_RULES = "My Junk Rules"
CAT_LEFTOVERS = "Program Leftovers"
CAT_FILE_TYPES = "Chosen File Types"
CAT_DUPLICATE_FOLDERS = "Duplicate Folders"
CAT_OLD_PROJECTS = "Old Project Builds"

# Risk Levels
RISK_SAFE = "Safe"
RISK_REVIEW = "Review Recommended"
RISK_CAUTION = "Caution"

# File Extension Definitions
INSTALLER_EXTENSIONS: Set[str] = {
    ".exe", ".msi", ".apk", ".dmg", ".pkg", ".deb", ".rpm",
    ".appx", ".msix", ".appxbundle", ".msixbundle"
}

# OS ISO Disks and Virtual Machine Images
DISK_IMAGE_EXTENSIONS: Set[str] = {
    ".iso", ".img", ".wim", ".esd",
    ".toast", ".nrg", ".cue", ".bin", ".mdf"
}
# Names also used for model weights, game data, SDK images and databases: listed as disc images
# only next to the matching cue sheet (game.bin + game.cue, disc.mdf + disc.mds)
DISC_IMAGE_SHEETS: Dict[str, Set[str]] = {".img": {".cue"}, ".bin": {".cue"}, ".mdf": {".mds"}}

# Virtual machine disks hold a whole computer's files: only ever listed as Caution
VM_DISK_EXTENSIONS: Set[str] = {".vhd", ".vhdx", ".avhdx", ".vmdk", ".qcow2", ".vdi", ".hdd"}

# Backup images made by backup tools (Acronis, Macrium, Veeam, Windows Backup): never flagged
BACKUP_IMAGE_EXTENSIONS: Set[str] = {".tib", ".tibx", ".mrimg", ".mrbak", ".vbk", ".vib", ".vrb", ".bkf"}

# Setup and Compressed Archive Files
ARCHIVE_EXTENSIONS: Set[str] = {
    ".zip", ".rar", ".7z", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".cab", ".z"
}

# Setup Scripts
SETUP_SCRIPT_EXTENSIONS: Set[str] = {
    ".bat", ".cmd", ".ps1", ".sh"
}

# Whole words in a script's name that clearly mean it installs something (install_deps.sh, setup.bat);
# words like "build" or "update" also name people's own scripts, so they don't count
INSTALLER_KEYWORDS: List[str] = [
    "setup", "install", "installer", "installation",
]

JAVA_EXTENSIONS: Set[str] = {
    ".class", ".war", ".ear"
}

BUILD_EXTENSIONS: Set[str] = {
    ".pyc", ".pyo", ".obj", ".o", ".pdb", ".idb", ".tlog",
    ".suo", ".user", ".orig"
}

BUILD_DIR_NAMES: Set[str] = {
    "node_modules", "__pycache__", ".pytest_cache", ".mypy_cache",
    ".ruff_cache", ".next", ".turbo", "target", "build", "out",
    "bin", "obj", ".gradle"
}

# Build outputs that share an extension with real user files (.obj 3D models, .pdb protein data,
# .user / .orig settings and merge backups), or may be the only copy of a program (.class, .pyc
# shipped without source), or are also other tools' data (.idb IDA Pro databases, .tlog
# Elasticsearch data): Safe only inside a build output folder or __pycache__
AMBIGUOUS_BUILD_EXTENSIONS: Set[str] = {
    ".obj", ".pdb", ".user", ".orig", ".class", ".pyc", ".pyo", ".idb", ".tlog",
}
BUILD_OUTPUT_FOLDER_NAMES: Set[str] = BUILD_DIR_NAMES | {"debug", "release", "x64", "x86"}
# Build folder names people also give their own folders (Videos/out, Models/Release, .venv/bin,
# ~/.gradle): they count as build output only with a project file next to them (or a few levels up)
PROJECT_BOUND_FOLDER_NAMES: Set[str] = {
    "bin", "obj", "out", "build", "target", ".gradle", "debug", "release", "x64", "x86",
}
# Files that mark a folder as a software project
PROJECT_MARKER_NAMES: Set[str] = {
    "package.json", "cargo.toml", "pom.xml", "build.gradle", "build.gradle.kts", "settings.gradle",
    "settings.gradle.kts", "cmakelists.txt", "makefile", "gnumakefile", "setup.py", "pyproject.toml",
    "go.mod", "meson.build", "build.xml", "package.swift", "composer.json", "build.sbt",
}
PROJECT_MARKER_SUFFIXES = (".sln", ".csproj", ".vbproj", ".fsproj", ".vcxproj", ".vcproj", ".xcodeproj")

# Database folders (LevelDB, IndexedDB) of browsers, Electron apps and wallets. Their ".log"
# files are write-ahead logs holding the newest data: nothing inside them is ever listed.
DATABASE_DIR_NAMES: Set[str] = {
    "leveldb", "indexeddb", "local storage", "session storage", "local extension settings",
    "sync data", "sync extension settings", "managed extension settings",
}
DATABASE_DIR_SUFFIXES = (".leveldb", ".indexeddb")

# Windows Installer's cache (needed to repair and uninstall programs): never scanned
PACKAGE_CACHE_DIR_NAMES: Set[str] = {"package cache"}

# Folders whose big files are program data, not forgotten files: games, Python environments and
# AI model stores. Never offered as duplicates or stale large files.
APP_DATA_DIR_NAMES: Set[str] = {
    "steamapps", "steamlibrary", ".venv", "venv", "site-packages", "envs", ".ollama", "ollama",
    "lm-studio", ".lmstudio", "lmstudio", "huggingface",
}

# Backup copies are often someone's only other copy, so they are never preselected
BACKUP_EXTENSIONS: Set[str] = {".bak", ".old"}

TEMP_EXTENSIONS: Set[str] = {
    ".tmp", ".temp", ".log", ".dmp", ".bak", ".old", ".thumb",
    "thumbs.db", ".ds_store", ".eslintcache"
}

BROKEN_DOWNLOAD_EXTENSIONS: Set[str] = {
    ".crdownload", ".part", ".partial", ".download"
}

# Version-control folders: never scanned, never flagged, never deleted from inside.
# (Git keeps empty folders such as .git/refs/heads that it needs to recognise the repo.)
VCS_DIR_NAMES: Set[str] = {".git", ".svn", ".hg", ".bzr"}

# Backup folders (lowercase names): never scanned or deleted from. Windows File History and system
# images, Time Machine, Carbon Copy Cloner's SafetyNet (what CCC kept aside on a backup disk), and
# Timeshift and Back In Time snapshots (Linux) on any disk
BACKUP_DIR_NAMES: Set[str] = {"filehistory", "windowsimagebackup", "backups.backupdb", "_ccc safetynet",
                              "timeshift", "timeshift-btrfs", "backintime"}
BACKUP_DIR_SUFFIXES = (".backupbundle", ".sparsebundle")

# Where JunkZero sets deleted items aside on drives that have no Recycle Bin (USB sticks, network shares)
HOLDING_DIR_NAME = ".JunkZero-holding"
HOLDING_DAYS = 7

# Deletes this big need the user to type DELETE to confirm
LARGE_DELETE_BYTES = 5 * 1024 ** 3
LARGE_DELETE_ITEMS = 1000

# System Protection Blacklist (Never scan into or delete from these folders)
SYSTEM_BLACKLIST_DIRS: Set[str] = {
    "windows", "system32", "syswow64", "winsxs", "$recycle.bin",
    "system volume information", "recovery", "boot", "perflogs",
    "program files", "program files (x86)", "programdata/microsoft"
}

# Protected Root Directories
PROTECTED_PATHS = [
    Path("C:/Windows"),
    Path("C:/Program Files"),
    Path("C:/Program Files (x86)"),
    Path("C:/ProgramData"),
]

# macOS: top-level system folders (paths starting with "/"). Nothing under them is scanned or
# deleted, except the per-user temp folders listed in MAC_SYSTEM_ALLOWED.
MAC_SYSTEM_ROOTS: Set[str] = {
    "system", "library", "applications", "usr", "bin", "sbin", "opt", "cores", "dev",
    "etc", "private", "var", "network",
}
MAC_SYSTEM_ALLOWED: List[str] = ["var/folders", "private/var/folders", "private/tmp"]

# macOS: inside ~/Library only these folders are scanned (and the folders leading to them);
# Mail, Messages, Keychains, Containers, iCloud Drive and the rest are left alone.
MAC_LIBRARY_ALLOWED: List[str] = [
    "caches", "logs", "application support",
    "developer/xcode/deriveddata", "developer/coresimulator/caches",
]
# ...and inside those, folders that belong to macOS itself or hold irreplaceable data
# (MobileSync holds iPhone and iPad backups)
MAC_LIBRARY_KEEP_NAMES: Set[str] = {
    "mobilesync", "addressbook", "callhistorydb", "callhistorytransactions", "clouddocs",
    "knowledge", "syncservices", "fileprovider", "icloud", "facetime", "dock", "quick look",
    "crashreporter", "accounts", "animoji", "diskimages", "apple", "cloudkit",
}
MAC_LIBRARY_KEEP_PREFIXES = ("com.apple.", "group.com.apple.")

# macOS: folders the system keeps on every disk (Spotlight index, Trash, version history)
MAC_VOLUME_METADATA_DIRS: Set[str] = {
    ".spotlight-v100", ".fseventsd", ".documentrevisions-v100", ".trashes", ".trash",
    ".mobilebackups", ".temporaryitems", ".vol",
}

# macOS: folders that Finder shows as a single file (apps, photo and music libraries).
# What's inside them is never scanned or deleted on its own.
MAC_PACKAGE_EXTENSIONS = (
    ".app", ".photoslibrary", ".photolibrary", ".migratedphotolibrary", ".aplibrary",
    ".musiclibrary", ".tvlibrary", ".imovielibrary", ".fcpbundle", ".logicx", ".band",
    ".framework", ".bundle", ".plugin", ".kext", ".xcarchive", ".sparsebundle", ".pages",
    ".numbers", ".key",
)

# Stale File Thresholds
STALE_DAYS_THRESHOLD = 180
LARGE_FILE_BYTES_THRESHOLD = 100 * 1024 * 1024  # 100 MB

# Duplicate detection only hashes files at least this big (smaller ones free little space)
DUPLICATE_MIN_BYTES = 1024 * 1024  # 1 MB

# Files in the Downloads folder untouched for this many days are listed for review
OLD_DOWNLOAD_DAYS = 90

# A coding project with nothing changed for this many days counts as old: its dependency and
# build folders (node_modules, .venv, target...) are listed for review under "Old Project Builds"
OLD_PROJECT_DAYS = 90

# The scan toggle that turns each category on (used to compare a scan with the previous one)
CATEGORY_OPTIONS: Dict[str, str] = {
    CAT_INSTALLERS: "include_installers",
    CAT_JAVA_BUILDS: "include_java_builds",
    CAT_TEMP_JUNK: "include_temp_junk",
    CAT_BROKEN_DOWNLOADS: "include_broken_downloads",
    CAT_STALE_LARGE: "include_stale_large",
    CAT_EMPTY_FOLDERS: "include_empty_folders",
    CAT_DUPLICATES: "include_duplicates",
    CAT_OLD_DOWNLOADS: "include_old_downloads",
    CAT_CUSTOM_RULES: "include_custom_rules",
    CAT_LEFTOVERS: "include_leftovers",
    CAT_FILE_TYPES: "include_file_types",
    CAT_DUPLICATE_FOLDERS: "include_duplicates",
    CAT_OLD_PROJECTS: "include_old_projects",
}


@dataclass
class ScanOptions:
    """Configurable options for a scan session."""
    target_path: str
    include_installers: bool = True
    include_java_builds: bool = True
    include_temp_junk: bool = True
    include_broken_downloads: bool = True
    include_stale_large: bool = True
    include_empty_folders: bool = True
    include_duplicates: bool = False
    include_old_downloads: bool = False
    include_custom_rules: bool = True
    # App data folders left behind by programs that are no longer installed (Windows and macOS)
    include_leftovers: bool = False
    # File type search: files whose extension is in file_types (e.g. ["mp4", "tar.gz"]),
    # optionally only those unchanged for file_type_min_days or more
    include_file_types: bool = False
    # Dependency and build folders of coding projects untouched for OLD_PROJECT_DAYS
    include_old_projects: bool = False
    file_types: List[str] = field(default_factory=list)
    file_type_min_days: int = 0
    old_download_days: int = OLD_DOWNLOAD_DAYS
    # Folders treated as "Downloads" for the Old Downloads category
    downloads_dirs: List[str] = field(default_factory=list)
    min_file_size_bytes: int = 0
    stale_days: int = STALE_DAYS_THRESHOLD
    skip_system_dirs: bool = True
    max_workers: int = 8
    # Further folders scanned in the same session (e.g. Windows junk locations)
    extra_paths: List[str] = field(default_factory=list)
    # User exclusion rules: folder/file paths, or glob patterns such as "*.iso"
    exclusions: List[str] = field(default_factory=list)
    # User junk rules (same syntax as exclusions): matches are flagged for review
    custom_rules: List[str] = field(default_factory=list)
    # Known junk locations (app.engine.locations.JunkLocation) whose contents are flagged
    junk_locations: List[Any] = field(default_factory=list)
    # Also scan every junk location as its own root (target_path may then be empty)
    scan_junk_locations: bool = False
    # Program leftover folders found before the scan (app.engine.leftovers.Leftover)
    # None: look for them on this machine when the scan starts
    leftovers: Optional[List[Any]] = None
    # What the user deleted and kept before (app.engine.smart.load_learning), used for scores
    learning: Dict[str, Any] = field(default_factory=dict)
    # Cloud-sync folders as (folder, provider) (app.engine.safeguards.cloud_folders); None: find them
    cloud_folders: Optional[List[Any]] = None
