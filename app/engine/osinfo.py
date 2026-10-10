"""Which operating system JunkZero is running on, and the words its UI uses for it."""
from __future__ import annotations
import os
import sys
from typing import Dict


def is_macos() -> bool:
    return sys.platform == "darwin"


def is_windows() -> bool:
    return os.name == "nt"


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
