"""Intelligent classification engine for detecting disk garbage and assessing safety risks."""
from __future__ import annotations
import os
import ntpath
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Optional
from pydantic import BaseModel

from app.config import (
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
    BUILD_EXTENSIONS,
    BUILD_DIR_NAMES,
    TEMP_EXTENSIONS,
    BROKEN_DOWNLOAD_EXTENSIONS,
    SYSTEM_BLACKLIST_DIRS,
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


def format_size(bytes_val: int) -> str:
    """Format bytes into a human-readable size string."""
    if bytes_val < 0:
        return "0 B"
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if bytes_val < 1024.0 or unit == "TB":
            return f"{bytes_val:.2f} {unit}" if unit != "B" else f"{bytes_val} B"
        bytes_val /= 1024.0
    return f"{bytes_val:.2f} TB"


def is_system_protected_path(path_str: str) -> bool:
    """Check if path is inside a protected Windows OS or application critical path."""
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

    return False


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

    category: Optional[str] = None
    risk_level: str = RISK_REVIEW
    reason: str = ""

    # 2. Incomplete / Broken Downloads
    if options.include_broken_downloads and suffix in BROKEN_DOWNLOAD_EXTENSIONS:
        category = CAT_BROKEN_DOWNLOADS
        risk_level = RISK_SAFE
        reason = f"Unfinished download ({suffix})"

    # 3. Temporary & Junk Files
    elif options.include_temp_junk and (suffix in TEMP_EXTENSIONS or lower_name in TEMP_EXTENSIONS):
        category = CAT_TEMP_JUNK
        risk_level = RISK_SAFE
        reason = f"Temporary/cache file ({suffix or lower_name})"

    # 4. Old Java Programs & Build Artifacts (.class, loose .jar, .pyc, .obj)
    elif options.include_java_builds and (suffix in JAVA_EXTENSIONS or suffix in BUILD_EXTENSIONS or suffix == ".jar"):
        category = CAT_JAVA_BUILDS
        if suffix == ".class":
            risk_level = RISK_SAFE
            reason = "Compiled Java class bytecode (.class)"
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

    # 5. OS ISO Disk Images, Virtual Disks, and Installation Media
    elif options.include_installers and suffix in DISK_IMAGE_EXTENSIONS:
        category = CAT_INSTALLERS
        risk_level = RISK_REVIEW
        reason = f"OS / Disc image file ({suffix.upper()})"

    # 6. Setup Archives and Compressed Packages (.zip, .rar, .7z, .tar.gz, etc.)
    elif options.include_installers and suffix in ARCHIVE_EXTENSIONS:
        norm_path = path.lower().replace("\\", "/")
        has_installer_kw = any(kw in lower_name for kw in INSTALLER_KEYWORDS)
        is_in_download_temp = any(p in norm_path for p in ["download", "temp", "desktop"])

        if has_installer_kw or is_in_download_temp:
            category = CAT_INSTALLERS
            risk_level = RISK_REVIEW
            if has_installer_kw:
                reason = f"Setup / software archive package ({suffix.upper()})"
            else:
                reason = f"Downloaded archive package in temporary folder ({suffix.upper()})"

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
            selected=(risk_level == RISK_SAFE)
        )

    return None
