"""Intelligent classification engine for detecting disk garbage and assessing safety risks."""
from __future__ import annotations
import os
import ntpath
import re
import time
from datetime import datetime
from pathlib import Path
from typing import List, Optional
from pydantic import BaseModel

from app.engine import osinfo
from app.engine.archives import inspect_archive

from app.config import (
    MAC_LIBRARY_ALLOWED,
    MAC_LIBRARY_KEEP_NAMES,
    MAC_LIBRARY_KEEP_PREFIXES,
    MAC_PACKAGE_EXTENSIONS,
    MAC_SYSTEM_ALLOWED,
    MAC_SYSTEM_ROOTS,
    MAC_VOLUME_METADATA_DIRS,
    LARGE_FILE_BYTES_THRESHOLD,
    CAT_INSTALLERS,
    CAT_JAVA_BUILDS,
    CAT_TEMP_JUNK,
    CAT_BROKEN_DOWNLOADS,
    CAT_STALE_LARGE,
    RISK_SAFE,
    RISK_REVIEW,
    RISK_CAUTION,
    INSTALLER_EXTENSIONS,
    DISK_IMAGE_EXTENSIONS,
    ARCHIVE_EXTENSIONS,
    SETUP_SCRIPT_EXTENSIONS,
    INSTALLER_KEYWORDS,
    JAVA_EXTENSIONS,
    AMBIGUOUS_BUILD_EXTENSIONS,
    BACKUP_EXTENSIONS,
    BACKUP_IMAGE_EXTENSIONS,
    BUILD_EXTENSIONS,
    BUILD_OUTPUT_FOLDER_NAMES,
    BUILD_DIR_NAMES,
    TEMP_EXTENSIONS,
    BROKEN_DOWNLOAD_EXTENSIONS,
    SYSTEM_BLACKLIST_DIRS,
    VM_DISK_EXTENSIONS,
    ScanOptions,
)


class GarbageItem(BaseModel):
    """Represents a flagged file or directory identified as garbage."""
    id: str
    name: str
    path: str
    category: str
    size_bytes: int
    size_formatted: str
    modified_timestamp: float
    modified_date: str
    risk_level: str
    reason: str
    is_directory: bool = False
    selected: bool = False
    # Smart score (app.engine.smart): 1-99 confidence that this is junk, and why
    score: int = 0
    score_reasons: List[str] = []
    recommendation: str = ""
    # Setup archives: what was found inside, and whether it was only program files
    archive_summary: str = ""
    archive_clean: bool = False
    # Safeguards (app.engine.safeguards): cloud-sync folder it sits in, and personal file types
    cloud_provider: str = ""
    personal: bool = False
    # Duplicates: the other copies of the same file (one must still exist when this one is deleted)
    duplicate_paths: List[str] = []


def format_size(bytes_val: int) -> str:
    """Format bytes into a human-readable size string."""
    if bytes_val < 0:
        return "0 B"
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if bytes_val < 1024.0 or unit == "TB":
            return f"{bytes_val:.2f} {unit}" if unit != "B" else f"{bytes_val} B"
        bytes_val /= 1024.0
    return f"{bytes_val:.2f} TB"


def build_item(
    path: str,
    name: str,
    category: str,
    size_bytes: int,
    mtime: float,
    risk_level: str,
    reason: str,
    is_dir: bool = False,
    selected: Optional[bool] = None,
) -> GarbageItem:
    """Construct a GarbageItem with formatted size/date; Safe items are preselected by default."""
    return GarbageItem(
        id=f"{'dir' if is_dir else 'file'}-{abs(hash((category, path)))}",
        name=name,
        path=path,
        category=category,
        size_bytes=size_bytes,
        size_formatted=format_size(size_bytes),
        modified_timestamp=mtime,
        modified_date=datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M") if mtime > 0 else "Unknown",
        risk_level=risk_level,
        reason=reason,
        is_directory=is_dir,
        selected=(risk_level == RISK_SAFE) if selected is None else selected,
    )


def is_system_protected_path(path_str: str) -> bool:
    """Check if path is inside a protected Windows or macOS system folder or application critical path."""
    normalized = ntpath.normpath(path_str).lower().replace("\\", "/")
    parts = normalized.split("/")

    for part in parts:
        if part in SYSTEM_BLACKLIST_DIRS:
            return True
        if part.startswith("$"):  # Windows system volume / recycle dirs
            return True

    # Windows root directory protection
    if re.match(r"^[a-z]:/(?:windows|program files(?: \(x86\))?|programdata)(?:/|$)", normalized):
        return True

    if "programdata/microsoft" in normalized:
        return True

    if parts[0] == "" and len(parts) > 1 and osinfo.is_macos() and _is_mac_protected(parts[1:]):
        return True

    return False


def _under(rel: List[str], allowed: str) -> bool:
    """True if rel is the allowed folder or inside it."""
    a = allowed.split("/")
    return rel[:len(a)] == a


def _leads_to(rel: List[str], allowed: str) -> bool:
    """True if rel is a folder on the way to the allowed folder (or the folder itself)."""
    a = allowed.split("/")
    return len(rel) <= len(a) and a[:len(rel)] == rel


def _is_mac_protected(rel: List[str]) -> bool:
    """macOS rules for an absolute POSIX path, given as its lowercase parts after the leading "/"."""
    rel = [p for p in rel if p]
    if not rel:
        return False
    # /System, /Library, /Applications, /usr, /private... (but not the per-user temp folders)
    if rel[0] in MAC_SYSTEM_ROOTS and not any(_under(rel, a) for a in MAC_SYSTEM_ALLOWED):
        return True
    # ~/Library: only caches, logs, app support and Xcode build data are looked at
    inside = _library_parts(rel)
    if inside is not None:
        if not any(_under(inside, a) or _leads_to(inside, a) for a in MAC_LIBRARY_ALLOWED):
            return True
        if len(inside) >= 2 and inside[0] in ("caches", "application support"):
            name = inside[1]
            if name in MAC_LIBRARY_KEEP_NAMES or name.startswith(MAC_LIBRARY_KEEP_PREFIXES):
                return True
    if any(p in MAC_VOLUME_METADATA_DIRS for p in rel):
        return True
    # Inside an app or a Photos / Music library (the package itself can still be removed)
    if any(p.endswith(MAC_PACKAGE_EXTENSIONS) for p in rel[:-1]):
        return True
    return False


def _library_parts(rel: List[str]) -> Optional[List[str]]:
    """The parts after "Library" if rel is in a user's Library folder (/Users/<name>/Library or
    the current home folder's), else None."""
    if len(rel) >= 3 and rel[0] == "users" and rel[2] == "library":
        return rel[3:]
    home = [p for p in os.environ.get("HOME", "").lower().split("/") if p]
    if home and len(rel) > len(home) and rel[:len(home)] == home and rel[len(home)] == "library":
        return rel[len(home) + 1:]
    return None


def _in_build_output_folder(path: str) -> bool:
    """True if any parent folder is a typical build output folder (bin, obj, build, Debug...)."""
    parents = ntpath.normpath(path).replace("\\", "/").lower().split("/")[:-1]
    return any(p in BUILD_OUTPUT_FOLDER_NAMES for p in parents)


# Folders whose files are disposable by nature: temp, cache, log and crash-dump folders, app data
_DISPOSABLE_FOLDER_NAMES = {
    "temp", "tmp", "logs", "log", "crashdumps", "crash reports", "diagnosticreports", "appdata",
    "application support", "local", "localappdata", "var",
}
# Temp-style names that are system clutter wherever they are
_SYSTEM_CLUTTER_NAMES = {"thumbs.db", ".ds_store"}


def _in_disposable_folder(path: str) -> bool:
    """True if a parent folder is a temp, cache, log, build output or hidden app-data folder."""
    parents = ntpath.normpath(path).replace("\\", "/").lower().split("/")[:-1]
    return any(
        p in _DISPOSABLE_FOLDER_NAMES or p in BUILD_OUTPUT_FOLDER_NAMES or "cache" in p
        or (p.startswith(".") and p not in (".", ".."))
        for p in parents
    )


def classify_item(
    path: str,
    name: str,
    size_bytes: int,
    mtime: float,
    is_dir: bool,
    options: ScanOptions
) -> Optional[GarbageItem]:
    """
    Examine a file or directory and determine if it qualifies as garbage.
    Returns GarbageItem if classified, or None if safe / excluded.
    """
    if options.skip_system_dirs and is_system_protected_path(path):
        return None

    lower_name = name.lower()
    suffix = Path(lower_name).suffix
    now = time.time()
    age_days = (now - mtime) / 86400.0 if mtime > 0 else 0

    # 1. Directory Checks (e.g. node_modules, __pycache__, target, build)
    if is_dir:
        if options.include_java_builds and lower_name in BUILD_DIR_NAMES:
            reason = f"Automated build or dependency directory ({name})"
            risk = RISK_SAFE if lower_name in {"__pycache__", ".pytest_cache", ".next", ".turbo"} else RISK_REVIEW
            mod_dt = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M") if mtime > 0 else "Unknown"
            return GarbageItem(
                id=f"dir-{hash(path)}",
                name=name,
                path=path,
                category=CAT_JAVA_BUILDS,
                size_bytes=size_bytes,
                size_formatted=format_size(size_bytes),
                modified_timestamp=mtime,
                modified_date=mod_dt,
                risk_level=risk,
                reason=reason,
                is_directory=True,
                selected=(risk == RISK_SAFE)
            )
        return None

    # File size filter
    if size_bytes < options.min_file_size_bytes:
        return None

    # Backup images and virtual machine disks hold whole computers' files: never listed
    if suffix in BACKUP_IMAGE_EXTENSIONS or suffix in VM_DISK_EXTENSIONS:
        return None

    category: Optional[str] = None
    risk_level: str = RISK_REVIEW
    reason: str = ""
    archive_summary: str = ""
    archive_clean: bool = False

    # 2. Incomplete / Broken Downloads
    if options.include_broken_downloads and suffix in BROKEN_DOWNLOAD_EXTENSIONS:
        category = CAT_BROKEN_DOWNLOADS
        risk_level = RISK_SAFE
        reason = f"Unfinished download ({suffix})"

    # 3. Temporary & Junk Files
    elif options.include_temp_junk and (suffix in TEMP_EXTENSIONS or lower_name in TEMP_EXTENSIONS):
        category = CAT_TEMP_JUNK
        if suffix in BACKUP_EXTENSIONS:
            risk_level = RISK_REVIEW
            reason = f"Backup copy ({suffix}); check you have the original"
        elif lower_name not in _SYSTEM_CLUTTER_NAMES and not _in_disposable_folder(path):
            # A .log or .tmp in Documents can be someone's own file: not listed at all
            return None
        else:
            risk_level = RISK_SAFE
            reason = f"Temporary/cache file ({suffix or lower_name})"

    # 4. Old Java Programs & Build Artifacts (.class, loose .jar, .pyc, .obj)
    elif options.include_java_builds and (suffix in JAVA_EXTENSIONS or suffix in BUILD_EXTENSIONS or suffix == ".jar"):
        category = CAT_JAVA_BUILDS
        if suffix == ".class":
            risk_level = RISK_SAFE
            reason = "Compiled Java class bytecode (.class)"
        elif suffix in AMBIGUOUS_BUILD_EXTENSIONS and not _in_build_output_folder(path):
            risk_level = RISK_REVIEW
            reason = f"Possible build artifact ({suffix}), but this file type is also used for real files"
        elif suffix in BUILD_EXTENSIONS:
            risk_level = RISK_SAFE
            reason = f"Compiler/build intermediate artifact ({suffix})"
        elif suffix == ".jar":
            # Determine if jar is in a downloads or temp folder or general location
            norm_path = path.lower().replace("\\", "/")
            if "download" in norm_path or "temp" in norm_path or "target" in norm_path:
                risk_level = RISK_REVIEW
                reason = "Java Archive (.jar) in temporary/build location"
            else:
                risk_level = RISK_CAUTION
                reason = "Java Archive (.jar) executable"

    # 5. OS ISO Disk Images and Installation Media
    elif options.include_installers and suffix in DISK_IMAGE_EXTENSIONS:
        category = CAT_INSTALLERS
        risk_level = RISK_REVIEW
        reason = f"OS / Disc image file ({suffix.upper()})"

    # 6. Setup Archives (.zip, .tar.gz, ...): only when the listing inside is clearly a software
    # package. The name alone never counts, because archives often hold personal files.
    elif options.include_installers and suffix in ARCHIVE_EXTENSIONS:
        verdict = inspect_archive(path, size_bytes)
        if verdict.is_setup:
            category = CAT_INSTALLERS
            risk_level = RISK_REVIEW
            reason = f"Setup archive: {verdict.summary}"
            archive_summary = verdict.summary
            archive_clean = verdict.clean

    # 7. Setup / Installation Scripts (.bat, .cmd, .ps1, .sh)
    elif options.include_installers and suffix in SETUP_SCRIPT_EXTENSIONS:
        has_installer_kw = any(kw in lower_name for kw in INSTALLER_KEYWORDS)
        if has_installer_kw:
            category = CAT_INSTALLERS
            risk_level = RISK_REVIEW
            reason = f"Setup / installation script ({suffix.upper()})"

    # 8. Installers, APKs, and Setup Executables
    elif options.include_installers and suffix in INSTALLER_EXTENSIONS:
        category = CAT_INSTALLERS
        if suffix == ".apk":
            risk_level = RISK_REVIEW
            reason = "Android application package (.apk)"
        elif suffix in {".msi", ".dmg", ".pkg", ".deb", ".rpm", ".appx", ".msix"}:
            risk_level = RISK_REVIEW
            reason = f"Software installation package ({suffix.upper()})"
        elif suffix == ".exe":
            norm_path = path.lower().replace("\\", "/")
            has_installer_kw = any(kw in lower_name for kw in INSTALLER_KEYWORDS)
            is_in_download_temp = any(p in norm_path for p in ["download", "temp", "desktop"])

            if has_installer_kw and is_in_download_temp:
                risk_level = RISK_REVIEW
                reason = "Installer setup executable in downloads/temp"
            elif has_installer_kw:
                risk_level = RISK_REVIEW
                reason = "Installer setup executable"
            elif is_in_download_temp:
                risk_level = RISK_REVIEW
                reason = "Standalone executable stored in download/temp directory"
            else:
                risk_level = RISK_CAUTION
                reason = "Executable file located in user directory"

    # 9. Stale Large Files
    elif options.include_stale_large and size_bytes >= LARGE_FILE_BYTES_THRESHOLD and age_days >= options.stale_days:
        category = CAT_STALE_LARGE
        risk_level = RISK_REVIEW
        reason = f"Large file ({format_size(size_bytes)}) unedited for {int(age_days)} days"

    if category:
        mod_dt = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M") if mtime > 0 else "Unknown"
        return GarbageItem(
            id=f"file-{abs(hash(path))}",
            name=name,
            path=path,
            category=category,
            size_bytes=size_bytes,
            size_formatted=format_size(size_bytes),
            modified_timestamp=mtime,
            modified_date=mod_dt,
            risk_level=risk_level,
            reason=reason,
            is_directory=False,
            selected=(risk_level == RISK_SAFE),
            archive_summary=archive_summary,
            archive_clean=archive_clean,
        )

    return None
