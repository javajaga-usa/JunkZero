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
