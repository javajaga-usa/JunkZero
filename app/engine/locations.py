"""Well-known Windows junk locations (temp folders, caches, crash dumps)."""
from __future__ import annotations
import glob
import os
from dataclasses import dataclass, asdict
from typing import Dict, List, Mapping, Optional

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
