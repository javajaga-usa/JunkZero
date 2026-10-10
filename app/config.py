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
    ".iso", ".img", ".vhd", ".vhdx", ".vmdk", ".qcow2", ".wim", ".esd",
    ".toast", ".nrg", ".cue", ".bin", ".mdf"
}

# Setup and Compressed Archive Files
ARCHIVE_EXTENSIONS: Set[str] = {
    ".zip", ".rar", ".7z", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".cab", ".z"
}

# Setup Scripts
SETUP_SCRIPT_EXTENSIONS: Set[str] = {
    ".bat", ".cmd", ".ps1", ".sh"
}

INSTALLER_KEYWORDS: List[str] = [
    "setup", "install", "installer", "update", "patch", "upgrade",
    "x64", "x86", "win64", "win32", "build", "dist", "release",
    "portable", "driver", "package", "pack", "bundle", "sdk",
    "windows", "win10", "win11", "ubuntu", "debian", "fedora", "arch",
    "linux", "edition", "x86_64", "amd64", "arm64"
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
# .user / .orig settings and merge backups): Safe only inside a build output folder
AMBIGUOUS_BUILD_EXTENSIONS: Set[str] = {".obj", ".pdb", ".user", ".orig"}
BUILD_OUTPUT_FOLDER_NAMES: Set[str] = BUILD_DIR_NAMES | {"debug", "release", "x64", "x86"}

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

# Stale File Thresholds
STALE_DAYS_THRESHOLD = 180
LARGE_FILE_BYTES_THRESHOLD = 100 * 1024 * 1024  # 100 MB

# Duplicate detection only hashes files at least this big (smaller ones free little space)
DUPLICATE_MIN_BYTES = 1024 * 1024  # 1 MB

# Files in the Downloads folder untouched for this many days are listed for review
OLD_DOWNLOAD_DAYS = 90

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
    # App data folders left behind by programs that are no longer installed (Windows only)
    include_leftovers: bool = False
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
