"""Intelligent classification engine for detecting disk garbage and assessing safety risks."""
from __future__ import annotations
import os
import ntpath
import re
import time
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import List, Optional
from pydantic import BaseModel

from app.engine import osinfo
from app.engine.archives import inspect_archive
from app.engine.installed import already_installed
from app.engine.installers import inside_installed_program, inspect_exe, is_uninstaller_name, named_like_installer

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
    APP_DATA_DIR_NAMES,
    DATABASE_DIR_NAMES,
    DATABASE_DIR_SUFFIXES,
    DISC_IMAGE_SHEETS,
    PACKAGE_CACHE_DIR_NAMES,
    PROJECT_BOUND_FOLDER_NAMES,
    PROJECT_MARKER_NAMES,
    PROJECT_MARKER_SUFFIXES,
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
    # The later of modified and created time (0: unknown). A file unpacked from an old archive
    # keeps the archive's modified date, but was created just now.
    changed_timestamp: float = 0.0
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
    # Setup programs (.exe): the installer fingerprint found, or True when only the name suggests it
    installer_evidence: str = ""
    installer_weak: bool = False
    # Setup programs (.exe): the program it installs is already installed at this or a newer version
    installed_note: str = ""


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


_USER_WINDOWS_CACHES = re.compile(
    r"/users/[^/]+/appdata/local/microsoft/windows/"
    r"(?:inetcache(?:/|$)|wer/reportarchive(?:/|$)|wer/reportqueue(?:/|$)|explorer/thumbcache_[^/]*\.db$)"
)


def is_system_protected_path(path_str: str) -> bool:
    """Check if path is inside a protected Windows or macOS system folder or application critical path."""
    normalized = ntpath.normpath(path_str).lower().replace("\\", "/")
    parts = normalized.split("/")

    # The user's own caches inside AppData\Local\Microsoft\Windows (IE/Edge INetCache, thumbnail
    # cache files, Windows Error Reporting archives): exact allow list, everything else stays protected
    if _USER_WINDOWS_CACHES.search(normalized) and not any(p.startswith("$") for p in parts):
        return False

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
    """The parts after "Library" if rel is in a user's Library folder (/Users/<name>/Library, the same
    on another disk or copy such as /Volumes/OldMac/Users/<name>/Library, or the current home folder's), else None."""
    for i in range(len(rel) - 2):
        if rel[i] == "users" and rel[i + 2] == "library":
            return rel[i + 3:]
    home = [p for p in os.environ.get("HOME", "").lower().split("/") if p]
    if home and len(rel) > len(home) and rel[:len(home)] == home and rel[len(home)] == "library":
        return rel[len(home) + 1:]
    return None


def _parent_parts(path: str) -> List[str]:
    """Lowercase names of the folders a path sits in (Windows or POSIX separators)."""
    return ntpath.normpath(path).replace("\\", "/").lower().split("/")[:-1]


@lru_cache(maxsize=8192)
def _listing(folder: str, stamp: int) -> frozenset:
    """Lowercase names in a folder (cached; stamp is the folder's mtime so changes are seen)."""
    try:
        return frozenset(n.lower() for n in os.listdir(folder))
    except OSError:
        return frozenset()


def folder_names(folder: str) -> frozenset:
    try:
        stamp = os.stat(folder).st_mtime_ns
    except OSError:
        return frozenset()
    return _listing(folder, stamp)


def has_project_marker(folder: str) -> bool:
    """True if the folder holds a project file (package.json, *.sln, CMakeLists.txt, Cargo.toml...)."""
    return any(n in PROJECT_MARKER_NAMES or n.endswith(PROJECT_MARKER_SUFFIXES) for n in folder_names(folder))


def _project_above(folder: str, levels: int) -> bool:
    """True if one of the `levels` folders above `folder` holds a project file."""
    for _ in range(levels):
        parent = ntpath.dirname(folder)
        if not parent or parent == folder:
            return False
        if has_project_marker(parent):
            return True
        folder = parent
    return False


def is_build_dir(path: str, name: str) -> bool:
    """True for a build or dependency folder (node_modules, __pycache__...). Names people also use
    for their own folders (bin, out, build, target, .gradle) count only next to a project file."""
    lower = name.lower()
    if lower not in BUILD_DIR_NAMES:
        return False
    return lower not in PROJECT_BOUND_FOLDER_NAMES or _project_above(path, 1)


def _in_build_output_folder(path: str) -> bool:
    """True if a parent folder is a build output folder (__pycache__, node_modules, or bin, obj,
    build, Debug, Release, x64... with a project file next to it or a few levels above)."""
    folder = ntpath.dirname(path)
    while folder and ntpath.dirname(folder) != folder:
        name = ntpath.basename(folder).lower()
        if name in BUILD_OUTPUT_FOLDER_NAMES:
            if name not in PROJECT_BOUND_FOLDER_NAMES or _project_above(folder, 3):
                return True
        folder = ntpath.dirname(folder)
    return False


# Folders whose files are disposable by nature: temp, cache, log and crash-dump folders.
# App-data folders (AppData, Local, Application Support, dot-folders) are not: apps keep their
# databases and settings there.
_DISPOSABLE_FOLDER_NAMES = {
    "temp", "tmp", "logs", "log", "crashdumps", "crash reports", "crashes", "crashpad", "minidump",
    "minidumps", "diagnosticreports", "_logs",
}
# Temp-style names that are system clutter wherever they are
_SYSTEM_CLUTTER_NAMES = {"thumbs.db", ".ds_store"}


def _in_disposable_folder(path: str) -> bool:
    """True if a parent folder is a temp, cache, log, crash-dump or build output folder."""
    parents = _parent_parts(path)
    if any(p in _DISPOSABLE_FOLDER_NAMES or "cache" in p for p in parents):
        return True
    # macOS per-user temp folder: /var/folders/xx/yyyy/T
    for i, p in enumerate(parents[:-3]):
        if p == "var" and parents[i + 1] == "folders" and parents[i + 4:i + 5] == ["t"]:
            return True
    return _in_build_output_folder(path)


def is_database_folder(name: str) -> bool:
    """True for a LevelDB / IndexedDB / Local Storage folder name."""
    lower = name.lower()
    return lower in DATABASE_DIR_NAMES or lower.endswith(DATABASE_DIR_SUFFIXES)


def is_leveldb_listing(names) -> bool:
    """True if a folder's names show a LevelDB database (a CURRENT file and a MANIFEST-* file)."""
    lowered = {n.lower() for n in names}
    return "current" in lowered and any(n.startswith("manifest-") for n in lowered)


def is_skipped_store(path: str) -> bool:
    """True for folders JunkZero never looks inside: app databases (LevelDB, IndexedDB, Local
    Storage), Windows' installer Package Cache and conda's package store."""
    parts = ntpath.normpath(path).replace("\\", "/").lower().split("/")
    for i, p in enumerate(parts):
        if is_database_folder(p) or p in PACKAGE_CACHE_DIR_NAMES:
            return True
        if p == "pkgs" and any(re.search(r"conda|mamba|miniforge", q) for q in parts[:i]):
            return True
    return False


def _in_app_folder(path: str) -> bool:
    """Big files that belong to an installed program, game, Python environment or AI model store."""
    parents = _parent_parts(path)
    if any(p in APP_DATA_DIR_NAMES for p in parents):
        return True
    return inside_installed_program(path)


def _disc_image_location_ok(path: str) -> bool:
    """Disc images are never looked for in app data or SDK folders (emulator and SDK images)."""
    return not any(
        p in {"appdata", "application support", "library"} or "sdk" in p or p in APP_DATA_DIR_NAMES
        for p in _parent_parts(path)
    )


# Volume descriptor ids at sectors 16-19 of an ISO 9660 or UDF disc image
_ISO_IDS = {b"CD001", b"BEA01", b"NSR02", b"NSR03", b"TEA01"}


def has_iso_signature(path: str) -> bool:
    try:
        with open(path, "rb") as f:
            for sector in range(16, 20):
                f.seek(sector * 2048 + 1)
                if f.read(5) in _ISO_IDS:
                    return True
    except OSError:
        return False
    return False


def _is_disc_image(path: str, suffix: str) -> bool:
    if not _disc_image_location_ok(path):
        return False
    if suffix == ".iso":
        return has_iso_signature(path)
    if suffix == ".cue":
        return False  # A cue sheet alone is a few bytes; its .bin is what is listed
    sheets = DISC_IMAGE_SHEETS.get(suffix)
    if sheets:
        stem = path[: -len(suffix)]
        return any(os.path.isfile(stem + s) or os.path.isfile(stem + s.upper()) for s in sheets)
    return True


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
    # App databases (LevelDB, IndexedDB), Package Cache, conda packages: never listed
    if is_skipped_store(path):
        return None

    lower_name = name.lower()
    suffix = Path(lower_name).suffix
    now = time.time()
    age_days = (now - mtime) / 86400.0 if mtime > 0 else 0

    # 1. Directory Checks (e.g. node_modules, __pycache__, target, build)
    if is_dir:
        if options.include_java_builds and is_build_dir(path, name):
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
    installer_evidence: str = ""
    installer_weak: bool = False
    installed_note: str = ""

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
        elif is_leveldb_listing(folder_names(os.path.dirname(path))):
            # A LevelDB database's .log is its newest data, not a log
            return None
        else:
            risk_level = RISK_SAFE
            reason = f"Temporary/cache file ({suffix or lower_name})"

    # 4. Old Java Programs & Build Artifacts (.class, loose .jar, .pyc, .obj)
    elif options.include_java_builds and (suffix in JAVA_EXTENSIONS or suffix in BUILD_EXTENSIONS or suffix == ".jar"):
        category = CAT_JAVA_BUILDS
        if suffix in AMBIGUOUS_BUILD_EXTENSIONS and not _in_build_output_folder(path):
            risk_level = RISK_REVIEW
            if suffix in {".class", ".pyc", ".pyo"}:
                reason = f"Compiled code ({suffix}) outside a build folder; it may be a program's only copy"
            else:
                reason = f"Possible build artifact ({suffix}), but this file type is also used for real files"
        elif suffix == ".class":
            risk_level = RISK_SAFE
            reason = "Compiled Java class bytecode (.class)"
        elif suffix in {".war", ".ear"}:
            risk_level = RISK_REVIEW
            reason = f"Packaged Java web application ({suffix}); may be a deployed or only copy"
        elif suffix in BUILD_EXTENSIONS:
            risk_level = RISK_SAFE
            reason = f"Compiler/build intermediate artifact ({suffix})"
        elif suffix == ".jar":
            # Only in downloads, temp or build output folders; elsewhere a .jar is a program,
            # a Minecraft mod or a Maven library: not listed
            parents = _parent_parts(path)
            if any("download" in p or p in {"temp", "tmp", "target"} for p in parents):
                risk_level = RISK_REVIEW
                reason = "Java Archive (.jar) in temporary/build location"
            else:
                return None

    # 5. OS ISO Disk Images and Installation Media (checked: ISO signature, or a cue sheet next to it)
    elif options.include_installers and suffix in DISK_IMAGE_EXTENSIONS:
        if not _is_disc_image(path, suffix):
            return None
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
        # Whole words only: "arch" in research.sh or "pack" in backup.sh mean nothing
        words = re.split(r"[^a-z]+", Path(lower_name).stem)
        has_installer_kw = any(kw in words for kw in INSTALLER_KEYWORDS)
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
            # Only setup programs count: look for an installer builder's fingerprint inside.
            # Portable apps, tools, games and uninstallers are left off the list.
            norm_path = path.lower().replace("\\", "/")
            is_in_download_temp = any(p in norm_path for p in ["download", "temp", "desktop"])
            category = None
            if not is_uninstaller_name(name) and not inside_installed_program(path):
                exe = inspect_exe(path)
                if exe.is_installer:
                    category = CAT_INSTALLERS
                    risk_level = RISK_REVIEW
                    reason = f"Setup program: {exe.evidence}"
                    installer_evidence = exe.evidence
                elif not exe.is_uninstaller and is_in_download_temp and named_like_installer(name):
                    category = CAT_INSTALLERS
                    risk_level = RISK_REVIEW
                    reason = "Named like a setup program, but no installer fingerprint was found inside"
                    installer_weak = True
                if category and exe.product:
                    # Its version info names what it installs: is that already on this computer?
                    installed_note = already_installed(exe.product, exe.version)
                    if installed_note:
                        reason += f"; {installed_note}"

    # 9. Stale Large Files
    elif options.include_stale_large and size_bytes >= LARGE_FILE_BYTES_THRESHOLD and age_days >= options.stale_days \
            and not _in_app_folder(path):
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
            installer_evidence=installer_evidence,
            installer_weak=installer_weak,
            installed_note=installed_note,
        )

    return None
