"""Find app data folders left behind by programs that are no longer installed.

Uninstallers often leave their settings and caches in %APPDATA% and %LOCALAPPDATA%.
A folder there is reported only when all of these hold, so a program that is still in
use is never flagged:
  * its name matches no installed program (name, publisher or install folder),
  * it is not one of the shared folders Windows and common tools keep there,
  * nothing inside it has changed for a long time (180 days by default).
If the list of installed programs can't be read, nothing is reported.
"""
from __future__ import annotations
import os
import re
import time
from dataclasses import dataclass
from typing import Iterable, List, Mapping, Optional, Set

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


def _is_shared(name: str) -> bool:
    lower = name.lower()
    return lower.startswith(".") or lower in _SHARED_FOLDERS or lower.startswith(_SHARED_PREFIXES)


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


def find_leftovers(
    roots: Iterable[str],
    installed: InstalledPrograms,
    min_days: int = LEFTOVER_MIN_DAYS,
    now: Optional[float] = None,
) -> List[Leftover]:
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
            if _is_shared(entry.name) or installed.matches(entry.name) or is_system_protected_path(entry.path):
                continue
            changed = newest_change(entry.path)
            days = int((now - changed) / 86400) if changed > 0 else 0
            if changed <= 0 or days < min_days:
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
