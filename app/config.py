"""Configuration settings and garbage classification rules for DiskPurge."""
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Set, Dict

# Category Constants
CAT_INSTALLERS = "Installers, Setup Archives & OS Images"
CAT_JAVA_BUILDS = "Old Java & Build Artifacts"
CAT_TEMP_JUNK = "Temporary & Cache Files"
CAT_BROKEN_DOWNLOADS = "Broken / Incomplete Downloads"
CAT_STALE_LARGE = "Stale Large Files"

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

TEMP_EXTENSIONS: Set[str] = {
    ".tmp", ".temp", ".log", ".dmp", ".bak", ".old", ".thumb",
    "thumbs.db", ".ds_store", ".eslintcache"
}

BROKEN_DOWNLOAD_EXTENSIONS: Set[str] = {
    ".crdownload", ".part", ".partial", ".download"
}

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


@dataclass
class ScanOptions:
    """Configurable options for a scan session."""
    target_path: str
    include_installers: bool = True
    include_java_builds: bool = True
    include_temp_junk: bool = True
    include_broken_downloads: bool = True
    include_stale_large: bool = True
    min_file_size_bytes: int = 0
    stale_days: int = STALE_DAYS_THRESHOLD
    skip_system_dirs: bool = True
    max_workers: int = 8
