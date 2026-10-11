"""Which operating system JunkZero is running on, and the words its UI uses for it."""
from __future__ import annotations
import os
import stat as _stat
import sys
from typing import Dict


def is_macos() -> bool:
    return sys.platform == "darwin"


def is_windows() -> bool:
    return os.name == "nt"


# Windows "hidden" and "system" file attributes
_WIN_HIDDEN_ATTRS = 0x2 | 0x4


def is_hidden(name: str, st: os.stat_result | None = None) -> bool:
    """True for dot-prefixed names, Windows hidden/system items and items macOS Finder hides.

    `st` is the item's own (not followed) stat result; without it only the name is checked."""
    if name.startswith("."):
        return True
    if st is None:
        return False
    if getattr(st, "st_file_attributes", 0) & _WIN_HIDDEN_ATTRS:
        return True
    return bool(getattr(st, "st_flags", 0) & getattr(_stat, "UF_HIDDEN", 0))


# Windows: offline, recall-on-open and recall-on-data-access attributes (cloud files not stored on this PC)
_WIN_ONLINE_ONLY_ATTRS = 0x1000 | 0x40000 | 0x400000
# macOS: SF_DATALESS, set on iCloud / File Provider files whose contents are only in the cloud
_MAC_DATALESS = 0x40000000


def is_online_only(st: os.stat_result) -> bool:
    """True for a cloud file whose contents are not on this computer (OneDrive "online-only",
    Dropbox / Google Drive / iCloud placeholders). Opening it would download it."""
    if getattr(st, "st_file_attributes", 0) & _WIN_ONLINE_ONLY_ATTRS:
        return True
    return is_macos() and bool(getattr(st, "st_flags", 0) & getattr(_stat, "SF_DATALESS", _MAC_DATALESS))


def platform_name() -> str:
    if is_windows():
        return "windows"
    if is_macos():
        return "macos"
    return "linux"


def platform_terms() -> Dict[str, object]:
    """Names the UI shows for this system's trash, file manager and scheduler."""
    if is_macos():
        return {
            "platform": "macos",
            "trash": "Trash",
            "file_manager": "Finder",
            "scheduler": "macOS launchd",
            "scheduling_supported": True,
        }
    return {
        "platform": platform_name(),
        "trash": "Recycle Bin",
        "file_manager": "Windows Explorer",
        "scheduler": "Windows Task Scheduler",
        "scheduling_supported": is_windows(),
    }
