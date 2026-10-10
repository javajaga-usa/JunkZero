"""Well-known junk locations (temp folders, caches, crash dumps) on Windows and macOS."""
from __future__ import annotations
import glob
import os
from dataclasses import dataclass, asdict
from typing import Dict, List, Mapping, Optional

from app.engine import osinfo
from app.engine.classifier import is_system_protected_path


@dataclass(frozen=True)
class JunkLocation:
    id: str
    label: str
    path: str
    # True: the folder itself is one disposable item (apps recreate it).
    # False: every file inside it is flagged individually.
    whole_dir: bool = False
    # Optional filename pattern (fnmatch, lowercase) limiting which files are flagged
    pattern: Optional[str] = None

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


# (id, label, path relative to LOCALAPPDATA, whole_dir, pattern).
# Folders under ...\Microsoft\Windows (thumbnail cache, WER, INetCache) are left out:
# JunkZero's safety filter protects every path containing a "Windows" folder.
_LOCAL_APPDATA_LOCATIONS = [
    ("crash_dumps", "Application crash dumps", "CrashDumps", False, None),
]

# (id prefix, browser name, profile glob relative to LOCALAPPDATA, cache subfolders)
_BROWSER_CACHES = [
    ("chrome", "Chrome", "Google/Chrome/User Data/*", ["Cache", "Code Cache", "GPUCache"]),
    ("edge", "Edge", "Microsoft/Edge/User Data/*", ["Cache", "Code Cache", "GPUCache"]),
    ("brave", "Brave", "BraveSoftware/Brave-Browser/User Data/*", ["Cache", "Code Cache", "GPUCache"]),
    ("firefox", "Firefox", "Mozilla/Firefox/Profiles/*", ["cache2"]),
]


def windows_junk_locations(env: Optional[Mapping[str, str]] = None) -> List[JunkLocation]:
    """Return the junk locations that exist for the current user.

    System-wide folders (C:\\Windows\\Temp, Windows Update downloads) live under
    protected paths that JunkZero never deletes from, so they are not offered.
    """
    env = os.environ if env is None else env
    local = env.get("LOCALAPPDATA")
    found: List[JunkLocation] = []
    seen = set()

    def add(loc: JunkLocation) -> None:
        key = (os.path.normcase(os.path.normpath(loc.path)), loc.pattern)
        if key in seen or not os.path.isdir(loc.path) or is_system_protected_path(loc.path):
            return
        seen.add(key)
        found.append(loc)

    temp = env.get("TEMP") or env.get("TMP") or (os.path.join(local, "Temp") if local else None)
    if temp:
        add(JunkLocation("user_temp", "User temp folder", os.path.normpath(temp)))

    if not local:
        return found

    for loc_id, label, rel, whole_dir, pattern in _LOCAL_APPDATA_LOCATIONS:
        add(JunkLocation(loc_id, label, os.path.normpath(os.path.join(local, rel)), whole_dir, pattern))

    for prefix, browser, profile_glob, subdirs in _BROWSER_CACHES:
        for profile in sorted(glob.glob(os.path.join(local, profile_glob))):
            profile_name = os.path.basename(profile)
            for sub in subdirs:
                add(JunkLocation(
                    f"{prefix}:{profile_name}:{sub}".lower().replace(" ", "_"),
                    f"{browser} {sub.lower()} ({profile_name})",
                    os.path.normpath(os.path.join(profile, sub)),
                    whole_dir=True,
                ))

    return found


def junk_locations(env: Optional[Mapping[str, str]] = None) -> List[JunkLocation]:
    """The junk locations for the system JunkZero is running on."""
    return mac_junk_locations(env) if osinfo.is_macos() else windows_junk_locations(env)


# Friendlier names for well-known folders in ~/Library/Caches (lowercase folder name -> label)
_MAC_CACHE_NAMES = {
    "google": "Google Chrome and other Google apps",
    "com.google.chrome": "Google Chrome",
    "bravesoftware": "Brave",
    "com.brave.browser": "Brave",
    "com.microsoft.edgemac": "Microsoft Edge",
    "firefox": "Firefox",
    "mozilla": "Firefox",
    "homebrew": "Homebrew downloads",
    "pip": "Python pip",
    "yarn": "Yarn",
    "com.spotify.client": "Spotify",
    "jetbrains": "JetBrains IDEs",
}

# (id, label, path relative to the home folder) of developer caches that tools rebuild
_MAC_DEV_CACHES = [
    ("xcode_derived_data", "Xcode build data (DerivedData)", "Library/Developer/Xcode/DerivedData"),
    ("simulator_caches", "iOS Simulator caches", "Library/Developer/CoreSimulator/Caches"),
    ("npm_cache", "npm download cache", ".npm/_cacache"),
]


def mac_junk_locations(env: Optional[Mapping[str, str]] = None) -> List[JunkLocation]:
    """Junk locations for the current macOS user.

    Each app's folder in ~/Library/Caches is listed as one item (apps rebuild it), except
    the ones macOS itself keeps there. Log files in ~/Library/Logs (including crash
    reports) and files in the per-user temp folder are flagged one by one.
    """
    env = os.environ if env is None else env
    home = env.get("HOME")
    found: List[JunkLocation] = []
    seen = set()

    def add(loc: JunkLocation) -> None:
        key = (os.path.normpath(loc.path).lower(), loc.pattern)
        if key in seen or not os.path.isdir(loc.path) or is_system_protected_path(loc.path):
            return
        seen.add(key)
        found.append(loc)

    temp = env.get("TMPDIR")
    if temp:
        add(JunkLocation("user_temp", "User temp folder", os.path.normpath(temp)))

    if not home:
        return found

    add(JunkLocation("mac_logs", "App logs and crash reports", os.path.join(home, "Library", "Logs")))

    caches = os.path.join(home, "Library", "Caches")
    try:
        entries = sorted(os.scandir(caches), key=lambda e: e.name.lower())
    except OSError:
        entries = []
    for entry in entries:
        try:
            if not entry.is_dir(follow_symlinks=False):
                continue
        except OSError:
            continue
        name = _MAC_CACHE_NAMES.get(entry.name.lower(), entry.name)
        add(JunkLocation(
            f"mac_cache:{entry.name}".lower().replace(" ", "_"), f"App cache ({name})",
            os.path.normpath(entry.path), whole_dir=True,
        ))

    for loc_id, label, rel in _MAC_DEV_CACHES:
        add(JunkLocation(loc_id, label, os.path.normpath(os.path.join(home, rel)), whole_dir=True))

    return found


# Known Folder ID of the user's Downloads folder (it can be moved to another drive)
_DOWNLOADS_GUID = "{374DE290-123F-4565-9164-39C4925E467B}"


def _registry_downloads() -> Optional[str]:
    try:
        import winreg
    except ImportError:
        return None
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders",
        ) as key:
            value, _ = winreg.QueryValueEx(key, _DOWNLOADS_GUID)
        return os.path.expandvars(value)
    except OSError:
        return None


def downloads_folder(env: Optional[Mapping[str, str]] = None) -> Optional[str]:
    """The current user's Downloads folder, or None if it can't be found."""
    env = os.environ if env is None else env
    candidates = [_registry_downloads()] if env is os.environ else []
    home = env.get("USERPROFILE") or env.get("HOME")
    if home:
        candidates.append(os.path.join(home, "Downloads"))
    for path in candidates:
        if path and os.path.isdir(path) and not is_system_protected_path(path):
            return os.path.normpath(path)
    return None


# Personal folders directly under the user profile. They can be emptied, but never deleted themselves.
_PERSONAL_FOLDER_NAMES = (
    "Desktop", "Documents", "Downloads", "Pictures", "Music", "Videos", "OneDrive", "AppData",
    "Favorites", "Contacts", "Links", "Saved Games", "Searches", "3D Objects",
)


# macOS: folders in the home folder that are never deleted whole
_MAC_PERSONAL_FOLDER_NAMES = (
    "Desktop", "Documents", "Downloads", "Pictures", "Music", "Movies", "Public", "Library",
    "Applications", "Sites", "iCloud Drive",
    "Library/Caches", "Library/Logs", "Library/Application Support", "Library/Developer",
    "Library/Developer/Xcode", "Library/Developer/CoreSimulator", "Library/Mobile Documents",
)


def _norm(path: str) -> str:
    norm = os.path.normcase(os.path.abspath(path)).rstrip("\\/")
    # macOS disks ignore case by default, like Windows
    return norm.lower() if osinfo.is_macos() else norm


def _is_drive_root(path: str) -> bool:
    return _norm(path) == _norm(os.path.splitdrive(os.path.abspath(path))[0] + os.sep)


def protected_user_folders(env: Optional[Mapping[str, str]] = None) -> set:
    """Normalized paths of the user profile, the folder holding all profiles, and the main personal folders."""
    env = os.environ if env is None else env
    home = env.get("USERPROFILE") or env.get("HOME")
    folders = set()
    if home:
        folders.add(_norm(home))
        users_dir = os.path.dirname(os.path.abspath(home))  # e.g. C:\Users
        if not _is_drive_root(users_dir):
            folders.add(_norm(users_dir))
        for name in _PERSONAL_FOLDER_NAMES:
            folders.add(_norm(os.path.join(home, name)))
        for name in ("Local", "LocalLow", "Roaming"):
            folders.add(_norm(os.path.join(home, "AppData", name)))
        if osinfo.is_macos():
            for name in _MAC_PERSONAL_FOLDER_NAMES:
                folders.add(_norm(os.path.join(home, *name.split("/"))))
    for key in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
        onedrive = env.get(key)
        if onedrive:
            folders.add(_norm(onedrive))
            for name in ("Desktop", "Documents", "Pictures"):
                folders.add(_norm(os.path.join(onedrive, name)))
    downloads = downloads_folder(env)
    if downloads:
        folders.add(_norm(downloads))
    return folders


def is_protected_user_folder(
    path: str, env: Optional[Mapping[str, str]] = None, folders: Optional[set] = None,
) -> bool:
    """True for the user profile itself, any profile folder (C:\\Users\\<name>) or a main personal folder.
    Pass folders (from protected_user_folders) when checking many paths."""
    env = os.environ if env is None else env
    norm = _norm(path)
    if norm in (folders if folders is not None else protected_user_folders(env)):
        return True
    home = env.get("USERPROFILE") or env.get("HOME")
    if home:
        users_dir = _norm(os.path.dirname(os.path.abspath(home)))
        # Other profiles next to the user's own (Public, Default, other accounts); skip when that's a drive root
        if not _is_drive_root(users_dir) and _norm(os.path.dirname(norm)) == users_dir:
            return True
    return False
