"""Find app data folders left behind by programs that are no longer installed.

Uninstallers often leave their settings and caches in %APPDATA% and %LOCALAPPDATA% on Windows,
and apps dragged to the Trash on macOS leave theirs in ~/Library/Application Support.
A folder there is reported only when all of these hold, so a program that is still in
use is never flagged:
  * its name matches no installed program (name, publisher or install folder),
  * it is not one of the shared folders the system and common tools keep there,
  * nothing inside it has changed for a long time (180 days by default),
  * nothing inside it is a personal file (documents, photos, videos, game saves).
If the list of installed programs can't be read, nothing is reported.
"""
from __future__ import annotations
import os
import plistlib
import re
import time
from dataclasses import dataclass
from typing import Iterable, List, Mapping, Optional, Set

from app.engine import osinfo
from app.engine.classifier import is_system_protected_path

LEFTOVER_MIN_DAYS = 180
# Entries looked at per folder when finding the newest change (keeps big folders fast)
_WALK_LIMIT = 3000

# Folders shared by Windows, drivers and developer tools, never one program's leftovers
_SHARED_FOLDERS: Set[str] = {
    "microsoft", "packages", "temp", "tmp", "programs", "comms", "connecteddevicesplatform",
    "d3dscache", "crashdumps", "publishers", "virtualstore", "peerdistrepub", "history",
    "identities", "isolatedstorage", "application data", "apps", "diagnostics",
    "elevateddiagnostics", "downloaded installations", "package cache", "placeholdertilelogofolder",
    "toastnotificationmanagercompat", "squirreltemp", "cache", "caches", "logs", "backup",
    "fontconfig", "gtk-3.0", "media center programs", "npm", "npm-cache", "pip", "nuget",
    "yarn", "pnpm", "pnpm-cache", "python", "sun", "java", "oracle", "intel", "nvidia",
    "nvidia corporation", "amd", "ati", "realtek", "adobe", "apple", "apple computer",
    "google", "mozilla", "junkzero", "pywebview", "cef", "electron", "github", "docker",
}
_SHARED_PREFIXES = ("microsoft", "windows", "msedge", "onedrive")


@dataclass(frozen=True)
class Leftover:
    path: str
    name: str
    last_changed: float  # Newest modification time found inside (0 if unknown)
    days_unchanged: int


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


class InstalledPrograms:
    """Names of installed programs, matched loosely against folder names."""

    def __init__(self, names: Iterable[str]):
        self.full: List[str] = []
        self.words: Set[str] = set()
        for name in names:
            if not isinstance(name, str):
                continue
            norm = _norm(name)
            if len(norm) >= 3:
                self.full.append(norm)
            self.words.update(w for w in (_norm(p) for p in re.split(r"[\s\-_.,()]+", name)) if len(w) >= 4)

    def __bool__(self) -> bool:
        return bool(self.full)

    def matches(self, folder_name: str) -> bool:
        folder = _norm(folder_name)
        if len(folder) < 3:
            return True  # Too short to tell; treat as in use
        if any(folder in name for name in self.full):
            return True
        # "Adobe Acrobat DC" belongs to "Adobe ...", but "OldGame Studio" is not "Visual Studio"
        return any(folder.startswith(word) for word in self.words)


# macOS: folders in ~/Library/Application Support that macOS and command-line tools share
_MAC_SHARED_FOLDERS: Set[str] = {
    "addressbook", "animoji", "app store", "callhistorydb", "callhistorytransactions",
    "clouddocs", "crashreporter", "diskimages", "dock", "facetime", "fileprovider", "icloud",
    "knowledge", "mobilesync", "quick look", "syncservices", "ubiquity", "accounts", "contacts",
    "networkserviceproxy", "siri", "spotlight", "familycircle", "cloudkit", "applemediaservices",
    "photos", "music", "tv", "podcasts", "books", "safari", "mail", "messages", "notes", "maps",
    "homebrew", "pypoetry", "virtualenv", "jupyter", "code", "jetbrains", "pip", "uv",
}
_MAC_SHARED_PREFIXES = ("com.apple.", "group.com.apple.", "apple")


def _is_shared(name: str, extra: Set[str] = frozenset(), extra_prefixes: tuple = ()) -> bool:
    lower = name.lower()
    return (lower.startswith(".") or lower in _SHARED_FOLDERS or lower.startswith(_SHARED_PREFIXES)
            or lower in extra or (bool(extra_prefixes) and lower.startswith(extra_prefixes)))


def newest_change(path: str, limit: int = _WALK_LIMIT) -> float:
    """Newest mtime of the folder and what's inside it (stops after `limit` entries)."""
    try:
        newest = os.stat(path, follow_symlinks=False).st_mtime
    except OSError:
        return 0.0
    seen = 0
    for root, dirs, files in os.walk(path):
        for name in dirs + files:
            seen += 1
            try:
                newest = max(newest, os.stat(os.path.join(root, name), follow_symlinks=False).st_mtime)
            except OSError:
                continue
            if seen >= limit:
                return newest
    return newest


# Entries looked at per folder for personal files; a bigger folder is not offered (it can't be checked fully)
_PERSONAL_WALK_LIMIT = 20000


def holds_personal_files(path: str, limit: int = _PERSONAL_WALK_LIMIT) -> bool:
    """True if the folder holds documents, photos, saves or other personal files (or is too big to check)."""
    from app.engine.safeguards import is_personal_file
    seen = 0
    for _, dirs, files in os.walk(path):
        for name in files:
            if is_personal_file(name):
                return True
        seen += len(dirs) + len(files)
        if seen > limit:
            return True
    return False


def find_leftovers(
    roots: Iterable[str],
    installed: InstalledPrograms,
    min_days: int = LEFTOVER_MIN_DAYS,
    now: Optional[float] = None,
    mac: bool = False,
) -> List[Leftover]:
    """Leftover folders directly inside roots. mac=True also skips the folders macOS shares."""
    if not installed:
        return []
    now = time.time() if now is None else now
    found: List[Leftover] = []
    for root in roots:
        try:
            entries = list(os.scandir(root))
        except OSError:
            continue
        for entry in entries:
            try:
                if not entry.is_dir(follow_symlinks=False):
                    continue
                is_junction = getattr(entry, "is_junction", None)
                if is_junction and is_junction():
                    continue
            except OSError:
                continue
            shared = _is_shared(entry.name, _MAC_SHARED_FOLDERS, _MAC_SHARED_PREFIXES) if mac else _is_shared(entry.name)
            if shared or installed.matches(entry.name) or is_system_protected_path(entry.path):
                continue
            changed = newest_change(entry.path)
            days = int((now - changed) / 86400) if changed > 0 else 0
            if changed <= 0 or days < min_days:
                continue
            # Game saves, notes, recordings or exports inside: left out entirely
            if holds_personal_files(entry.path):
                continue
            found.append(Leftover(entry.path, entry.name, changed, days))
    return found


# ---------------------------------------------------------------- This machine (Windows)

_UNINSTALL_KEYS = [
    r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
    r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
]


def _registry_programs() -> List[str]:
    try:
        import winreg
    except ImportError:
        return []
    names: List[str] = []
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for key_path in _UNINSTALL_KEYS:
            try:
                key = winreg.OpenKey(hive, key_path)
            except OSError:
                continue
            with key:
                for i in range(winreg.QueryInfoKey(key)[0]):
                    try:
                        sub_name = winreg.EnumKey(key, i)
                        with winreg.OpenKey(key, sub_name) as sub:
                            for value in ("DisplayName", "Publisher", "InstallLocation"):
                                try:
                                    data = winreg.QueryValueEx(sub, value)[0]
                                except OSError:
                                    continue
                                if isinstance(data, str) and data.strip():
                                    names.append(os.path.basename(data.strip().rstrip("\\/")) if value == "InstallLocation" else data)
                    except OSError:
                        continue
    return names


def installed_programs(env: Optional[Mapping[str, str]] = None) -> InstalledPrograms:
    """Programs in the Windows uninstall list, plus folders in Program Files."""
    env = os.environ if env is None else env
    names = _registry_programs()
    # Without the uninstall list most installed programs are unknown: report nothing
    if len(names) < 10:
        return InstalledPrograms([])
    folders = [env.get("ProgramFiles"), env.get("ProgramFiles(x86)"), env.get("ProgramW6432")]
    if env.get("LOCALAPPDATA"):
        folders.append(os.path.join(env["LOCALAPPDATA"], "Programs"))
    for folder in folders:
        if not folder:
            continue
        try:
            names.extend(e.name for e in os.scandir(folder) if e.is_dir(follow_symlinks=False))
        except OSError:
            continue
    return InstalledPrograms(names)


def app_data_roots(env: Optional[Mapping[str, str]] = None) -> List[str]:
    env = os.environ if env is None else env
    return [p for p in (env.get("APPDATA"), env.get("LOCALAPPDATA")) if p and os.path.isdir(p)]


def find_windows_leftovers() -> List[Leftover]:
    roots = app_data_roots()
    if not roots:
        return []
    return find_leftovers(roots, installed_programs())


# ---------------------------------------------------------------- This machine (macOS)

def _app_names(app_path: str) -> List[str]:
    """An app's file name plus the name, executable and bundle id in its Info.plist."""
    names = [os.path.splitext(os.path.basename(app_path))[0]]
    try:
        with open(os.path.join(app_path, "Contents", "Info.plist"), "rb") as f:
            info = plistlib.load(f)
    except (OSError, plistlib.InvalidFileException, ValueError):
        return names
    if isinstance(info, dict):
        for key in ("CFBundleIdentifier", "CFBundleName", "CFBundleDisplayName", "CFBundleExecutable"):
            value = info.get(key)
            if isinstance(value, str) and value.strip():
                names.append(value.strip())
    return names


def _list_names(folder: str, suffix: str = "") -> List[str]:
    """Names (without suffix) of the entries in folder that end with suffix."""
    try:
        return [os.path.splitext(e.name)[0] if suffix else e.name
                for e in os.scandir(folder) if e.name.endswith(suffix) and not e.name.startswith(".")]
    except OSError:
        return []


def mac_installed_programs(home: Optional[str] = None, root: str = "/") -> InstalledPrograms:
    """Apps in /Applications (and the system's own apps, background services, frameworks and
    Homebrew packages), matched against folder names. Empty if the app list can't be read."""
    home = home if home is not None else os.path.expanduser("~")
    r = lambda *parts: os.path.join(root, *parts)
    app_dirs = [r("Applications"), r("Applications", "Utilities"), r("System", "Applications"),
                r("System", "Applications", "Utilities"), r("System", "Library", "CoreServices")]
    if home:
        app_dirs.append(os.path.join(home, "Applications"))
    names: List[str] = []
    app_count = 0
    for folder in app_dirs:
        try:
            entries = list(os.scandir(folder))
        except OSError:
            continue
        for entry in entries:
            if entry.name.endswith(".app"):
                app_count += 1
                names.extend(_app_names(entry.path))
            elif entry.is_dir(follow_symlinks=False) and not entry.name.startswith("."):
                # Suites keep their apps in a folder ("/Applications/Microsoft Office/...")
                names.append(entry.name)
                for inner in _list_names(entry.path, ".app"):
                    app_count += 1
                    names.extend(_app_names(os.path.join(entry.path, inner + ".app")))
    # Without the list of apps most programs are unknown: report nothing
    if app_count < 10:
        return InstalledPrograms([])
    launch_dirs = [r("System", "Library", "LaunchAgents"), r("System", "Library", "LaunchDaemons"),
                   r("Library", "LaunchAgents"), r("Library", "LaunchDaemons")]
    if home:
        launch_dirs.append(os.path.join(home, "Library", "LaunchAgents"))
    for folder in launch_dirs:
        names.extend(_list_names(folder, ".plist"))
    for folder in (r("System", "Library", "Frameworks"), r("System", "Library", "PrivateFrameworks"),
                   r("Library", "Frameworks")):
        names.extend(_list_names(folder, ".framework"))
    for folder in (r("opt", "homebrew", "Cellar"), r("opt", "homebrew", "Caskroom"),
                   r("usr", "local", "Cellar"), r("usr", "local", "Caskroom")):
        names.extend(_list_names(folder))
    return InstalledPrograms(names)


def mac_app_data_roots(home: Optional[str] = None) -> List[str]:
    home = home if home is not None else os.path.expanduser("~")
    support = os.path.join(home, "Library", "Application Support") if home else ""
    return [support] if support and os.path.isdir(support) else []


def find_mac_leftovers() -> List[Leftover]:
    roots = mac_app_data_roots()
    if not roots:
        return []
    return find_leftovers(roots, mac_installed_programs(), mac=True)


def find_program_leftovers() -> List[Leftover]:
    """Leftovers on the system JunkZero is running on."""
    return find_mac_leftovers() if osinfo.is_macos() else find_windows_leftovers()
