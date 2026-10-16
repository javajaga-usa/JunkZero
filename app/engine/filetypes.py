"""File type search: find files with the extensions the user picks, so they can be added to
the deletion list. Matches are scan results like any other category, so every safeguard
(exclusions, protected folders, cloud and personal files, DELETE typed) still applies."""
from __future__ import annotations
import re
import time
from typing import Dict, List, Optional, Sequence, Tuple

from app.config import BACKUP_IMAGE_EXTENSIONS, VM_DISK_EXTENSIONS

# At most this many types in one search
MAX_FILE_TYPES = 100
# Letters, digits and a few symbols, with dots between parts ("tar.gz"); no paths or wildcards
_TYPE_PATTERN = re.compile(r"^[a-z0-9_+~$-]{1,16}(\.[a-z0-9_+~$-]{1,16}){0,2}$")

# Ready-made groups shown as chips in the UI (id -> label and extensions without the dot)
PRESETS: Dict[str, Tuple[str, Tuple[str, ...]]] = {
    "videos": ("Videos", ("mp4", "mov", "m4v", "avi", "mkv", "wmv", "flv", "webm", "mpg", "mpeg", "3gp", "mts")),
    "images": ("Photos & images", ("jpg", "jpeg", "png", "gif", "bmp", "tif", "tiff", "heic", "webp", "psd",
                                    "raw", "cr2", "cr3", "nef", "arw", "dng")),
    "audio": ("Music & audio", ("mp3", "wav", "flac", "aac", "m4a", "ogg", "wma", "aiff", "opus")),
    "documents": ("Documents", ("pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "rtf", "odt", "ods", "odp",
                                "txt", "csv", "pages", "numbers", "key")),
    "archives": ("Archives", ("zip", "rar", "7z", "tar", "gz", "tgz", "bz2", "xz")),
    "disk_images": ("Disk images", ("iso", "img", "dmg", "wim", "esd")),
    "installers": ("Installers", ("exe", "msi", "pkg", "apk", "deb", "rpm", "appx", "msix")),
    "logs_temp": ("Logs, temp & backups", ("log", "tmp", "temp", "bak", "old", "dmp", "chk")),
    "dev": ("Developer output", ("class", "jar", "pyc", "o", "obj", "pdb", "ilk", "map")),
}


class FileTypeError(ValueError):
    """A file type the search can't use (shown to the user as is)."""


def presets() -> List[Dict[str, object]]:
    return [{"id": key, "label": label, "types": list(types)} for key, (label, types) in PRESETS.items()]


def never_listed() -> List[str]:
    """Types the search refuses: backup images and virtual machine disks (without the dot)."""
    return sorted(e.lstrip(".") for e in BACKUP_IMAGE_EXTENSIONS | VM_DISK_EXTENSIONS)


def normalize_types(raw: Sequence[str]) -> List[str]:
    """Clean user input into lowercase extensions without the leading dot, in order, without
    repeats. Accepts "mp4", ".mp4", "*.mp4" and "tar.gz". Raises FileTypeError on anything else."""
    types: List[str] = []
    for value in raw:
        if not isinstance(value, str):
            raise FileTypeError("File types must be text such as .mp4")
        text = value.strip().lower()
        if text.strip("*.") == "" and "*" in text:
            raise FileTypeError(f"'{value.strip()}' would list every file. Use an extension such as .mp4")
        if text.startswith("*"):
            text = text[1:]
        text = text.lstrip(".")
        if not text:
            continue
        if not _TYPE_PATTERN.match(text):
            raise FileTypeError(f"'{value.strip()}' is not a file type. Use an extension such as .mp4 or .tar.gz")
        last = "." + text.rsplit(".", 1)[-1]
        if last in BACKUP_IMAGE_EXTENSIONS:
            raise FileTypeError(f".{text} files are backup images, which JunkZero never lists")
        if last in VM_DISK_EXTENSIONS:
            raise FileTypeError(f".{text} files are virtual machine disks, which JunkZero never lists")
        if text not in types:
            types.append(text)
    if len(types) > MAX_FILE_TYPES:
        raise FileTypeError(f"Pick at most {MAX_FILE_TYPES} file types")
    return types


class FileTypeMatcher:
    """Matches file names against the chosen types; the longest type wins (".tar.gz" over ".gz")."""

    def __init__(self, types: Sequence[str], min_age_days: int = 0):
        self.types = sorted({t.lower().lstrip(".") for t in types if t}, key=len, reverse=True)
        self._suffixes = [("." + t, t) for t in self.types]
        self.min_age_days = max(0, int(min_age_days or 0))

    def __bool__(self) -> bool:
        return bool(self.types)

    def match(self, name: str) -> Optional[str]:
        lower = name.lower()
        for suffix, ext in self._suffixes:
            # A file named only ".log" is a hidden file with no type, not a log
            if lower.endswith(suffix) and len(lower) > len(suffix):
                return ext
        return None

    def old_enough(self, mtime: float, now: Optional[float] = None) -> bool:
        if not self.min_age_days:
            return True
        if mtime <= 0:
            return False
        return ((now if now is not None else time.time()) - mtime) / 86400.0 >= self.min_age_days
