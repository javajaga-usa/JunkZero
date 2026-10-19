"""Space JunkZero's cleanups still hold: what sits in the Recycle Bin / Trash and the holding
folders, and emptying only JunkZero's own items from them.

Moving an item to the Recycle Bin frees no space until the Recycle Bin is emptied. JunkZero
can empty just what it put there itself, and only items it can point to exactly: on Windows
the Recycle Bin entry whose original path and deletion time match the cleanup, on a Mac the
very item (by device and inode) it moved to the Trash, and the items in its own holding
folders. Anything else in the Recycle Bin or Trash is never touched.
"""
from __future__ import annotations
import os
import re
import shutil
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import unquote

from app.config import HOLDING_DIR_NAME
from app.engine import osinfo, permissions, recycle, storage
from app.engine.classifier import format_size

# Cleanups at least this old can be emptied (the user can pick another number of days)
EMPTY_AFTER_DAYS = 7
# A Recycle Bin / Trash item is matched to a cleanup only when it was deleted within this many
# seconds of the cleanup's start and end
_WINDOW = 120
# Files looked at when measuring one stored folder, before giving up on its size
_SIZE_LIMIT = 200000

KIND_TRASH, KIND_HOLDING = "trash", "holding"


@dataclass
class StoredItem:
    """One item of a cleanup that still takes space: where it went and what it takes up."""
    timestamp: float          # The cleanup it belongs to
    original: str             # Where it was
    stored: str               # Where it is now (Recycle Bin, Trash or holding folder)
    kind: str                 # KIND_TRASH or KIND_HOLDING
    size: int
    extra: List[str]          # Files to remove with it (the Recycle Bin's $I file, a .trashinfo)


def _size(path: str, limit: int = _SIZE_LIMIT) -> int:
    try:
        st = os.lstat(path)
    except OSError:
        return 0
    if not os.path.isdir(path) or os.path.islink(path):
        return st.st_size
    total, seen = 0, 0
    for root, dirs, files in os.walk(path):
        dirs[:] = [d for d in dirs if not os.path.islink(os.path.join(root, d))]
        for name in files:
            seen += 1
            if seen > limit:
                return total
            try:
                total += os.lstat(os.path.join(root, name)).st_size
            except OSError:
                pass
    return total


def _parts(path: str) -> List[str]:
    return os.path.normcase(os.path.abspath(path)).replace("\\", "/").split("/")


def _in_holding(path: str) -> bool:
    return HOLDING_DIR_NAME.lower() in [p.lower() for p in _parts(path)[:-1]]


def _in_trash(path: str) -> bool:
    """True if path lies inside a Recycle Bin or Trash folder (never anywhere else)."""
    parts = [p.lower() for p in _parts(path)[:-1]]
    if osinfo.is_windows():
        return "$recycle.bin" in parts
    if osinfo.is_macos():
        return ".trash" in parts or ".trashes" in parts
    return "trash" in parts or any(p.startswith(".trash-") for p in parts) or ".trash" in parts


def _windows_match(original: str, since: float, until: float,
                   bins: Dict[str, recycle.RecycleIndex]) -> Optional[Tuple[str, List[str]]]:
    """The Recycle Bin entry for original deleted between since and until (the newest one)."""
    bin_root = os.path.join(recycle.mount_point(original), "$Recycle.Bin")
    if bin_root not in bins:
        bins[bin_root] = recycle.windows_recycle_index(bin_root)
    best = None
    for deleted, stored, info_file in bins[bin_root].get(recycle._norm(original), []):
        if since <= deleted <= until and os.path.lexists(stored) and (best is None or deleted > best[0]):
            best = (deleted, stored, info_file)
    return (best[1], [best[2]]) if best else None


def _find_stored(original: str, record: Dict[str, Any], bins: Dict[str, recycle.RecycleIndex]
                 ) -> Optional[Tuple[str, str, List[str]]]:
    """(kind, stored path, extra files) for one item of a cleanup, or None when JunkZero can't
    point to it exactly (already emptied or restored, or the match would be a guess)."""
    held = record.get("held") or {}
    if original in held:
        stored = held[original]
        if isinstance(stored, str) and _in_holding(stored) and os.path.lexists(stored):
            return KIND_HOLDING, stored, []
        return None
    since = float(record.get("started_at") or record.get("timestamp") or 0) - _WINDOW
    until = float(record.get("timestamp") or since) + _WINDOW
    if osinfo.is_windows():
        found = _windows_match(original, since, until, bins)
    elif osinfo.is_macos():
        file_id = (record.get("trash_ids") or {}).get(original)
        if not file_id:
            return None  # Only the very item JunkZero moved there; a name match is a guess
        found = recycle.find_in_mac_trash(original, since, file_id=file_id)
    else:
        found = _xdg_match(original, since, until)
    if not found or not _in_trash(found[0]):
        return None
    return KIND_TRASH, found[0], list(found[1])


def _xdg_match(original: str, since: float, until: float) -> Optional[Tuple[str, List[str]]]:
    """The Trash item for original deleted between since and until (the newest one)."""
    best: Optional[Tuple[float, str, str]] = None
    for trash, top in recycle._xdg_trash_dirs(original):
        info_dir = os.path.join(trash, "info")
        try:
            entries = [e for e in os.scandir(info_dir) if e.name.endswith(".trashinfo")]
        except OSError:
            continue
        for entry in entries:
            try:
                with open(entry.path, encoding="utf-8") as f:
                    text = f.read(8192)
                path_m = re.search(r"^Path=(.+)$", text, re.M)
                date_m = re.search(r"^DeletionDate=(\S+)$", text, re.M)
                if not (path_m and date_m):
                    continue
                deleted = time.mktime(datetime.strptime(date_m.group(1), "%Y-%m-%dT%H:%M:%S").timetuple())
            except (OSError, UnicodeDecodeError, ValueError):
                continue
            path = unquote(path_m.group(1).strip())
            if top and not os.path.isabs(path):
                path = os.path.join(top, path)
            if not recycle._same(path, original) or not since <= deleted <= until:
                continue
            stored = os.path.join(trash, "files", entry.name[:-len(".trashinfo")])
            if os.path.lexists(stored) and (best is None or deleted > best[0]):
                best = (deleted, stored, entry.path)
    return (best[1], [best[2]]) if best else None


def stored_items(history: Optional[List[Dict[str, Any]]] = None) -> List[StoredItem]:
    """Every item of JunkZero's cleanups that is still in the Recycle Bin / Trash or a holding
    folder, newest cleanup first."""
    items: List[StoredItem] = []
    bins: Dict[str, recycle.RecycleIndex] = {}  # Each Recycle Bin is read once
    for summary in storage.load_history() if history is None else history:
        if not isinstance(summary, dict) or summary.get("mode") == "permanent":
            continue
        record = storage.find_cleanup(summary.get("timestamp")) or summary
        done = set(record.get("restored") or []) | set(record.get("emptied") or [])
        for original in record.get("paths") or []:
            if not isinstance(original, str) or original in done:
                continue
            try:
                found = _find_stored(original, record, bins)
            except OSError:
                found = None
            if found is None:
                continue
            kind, stored, extra = found
            items.append(StoredItem(float(record.get("timestamp") or 0), original, stored, kind,
                                    _size(stored), extra))
    return items


def recycle_bin_size() -> Optional[int]:
    """How much the whole Recycle Bin / Trash takes up (everything in it, not only JunkZero's),
    or None if it can't be told."""
    if osinfo.is_windows():
        try:
            import ctypes

            class _Info(ctypes.Structure):
                _fields_ = [("cbSize", ctypes.c_ulong), ("i64Size", ctypes.c_longlong),
                            ("i64NumItems", ctypes.c_longlong)]
            info = _Info()
            info.cbSize = ctypes.sizeof(_Info)
            if ctypes.windll.shell32.SHQueryRecycleBinW(None, ctypes.byref(info)) != 0:
                return None
            return int(info.i64Size)
        except (OSError, AttributeError, ValueError):
            return None
    if osinfo.is_macos():
        trash = os.path.expanduser("~/.Trash")
    else:
        trash = os.path.join(os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share"), "Trash", "files")
    if not os.path.isdir(trash):
        return 0
    try:
        return sum(_size(e.path) for e in os.scandir(trash))
    except OSError:
        return None


def summary(older_than_days: int = EMPTY_AFTER_DAYS, now: Optional[float] = None) -> Dict[str, Any]:
    """What JunkZero's cleanups still hold, and how much of it is from cleanups at least
    older_than_days old (what "Empty" would free)."""
    now = time.time() if now is None else now
    items = stored_items()
    cutoff = now - older_than_days * 86400
    old = [i for i in items if i.timestamp <= cutoff]
    total_bin = recycle_bin_size()

    def bytes_of(chosen: List[StoredItem], kind: Optional[str] = None) -> int:
        return sum(i.size for i in chosen if kind is None or i.kind == kind)

    return {
        "recycle_bin_bytes": total_bin,
        "recycle_bin_formatted": format_size(total_bin) if total_bin is not None else "",
        "junkzero_count": len(items),
        "junkzero_bytes": bytes_of(items),
        "junkzero_formatted": format_size(bytes_of(items)),
        "trash_bytes": bytes_of(items, KIND_TRASH),
        "holding_bytes": bytes_of(items, KIND_HOLDING),
        "older_than_days": older_than_days,
        "old_count": len(old),
        "old_bytes": bytes_of(old),
        "old_formatted": format_size(bytes_of(old)),
    }


def _erase(path: str) -> None:
    """Remove path for good. The user's own read-only files and folders inside (the Windows
    read-only attribute, files copied off a disc, Go's module cache) are made writable as they
    are reached, together with the folder holding them."""
    if os.path.isdir(path) and not os.path.islink(path):
        if sys.version_info >= (3, 12):
            shutil.rmtree(path, onexc=lambda func, p, exc: permissions.retry_writable(func, p, exc))
        else:
            shutil.rmtree(path, onerror=lambda func, p, exc_info: permissions.retry_writable(func, p, exc_info[1]))
    else:
        permissions.with_retry(lambda: os.remove(path), path)


def empty_old(older_than_days: int = EMPTY_AFTER_DAYS, now: Optional[float] = None) -> Dict[str, Any]:
    """Erase, for good, JunkZero's own items from cleanups at least older_than_days old. Only
    items found exactly (see stored_items) are touched; everything else in the Recycle Bin or
    Trash stays. Returns {"emptied", "freed_bytes", "errors"}."""
    now = time.time() if now is None else now
    cutoff = now - older_than_days * 86400
    emptied: Dict[float, List[str]] = {}
    errors: List[Dict[str, str]] = []
    freed = 0
    for item in stored_items():
        if item.timestamp > cutoff:
            continue
        inside = _in_holding(item.stored) if item.kind == KIND_HOLDING else _in_trash(item.stored)
        if not inside:
            continue  # Never erase anything outside the Recycle Bin, Trash or holding folder
        try:
            _erase(item.stored)
        except OSError as e:
            why = permissions.explain(e, item.stored)
            errors.append({"path": item.original, "error": f"Could not empty it: {why[1] if why else e.strerror or e}"})
            continue
        for extra in item.extra:
            if _in_trash(extra):
                try:
                    os.remove(extra)
                except OSError:
                    pass
        if item.kind == KIND_HOLDING:
            batch = recycle._batch_of(item.stored)
            if batch:
                recycle._remove_empty_dirs(os.path.dirname(item.stored), stop_at=batch[0])
                try:
                    os.rmdir(batch[0])
                except OSError:
                    pass
        freed += item.size
        emptied.setdefault(item.timestamp, []).append(item.original)
    for timestamp, paths in emptied.items():
        storage.mark_emptied(timestamp, paths)
    return {
        "emptied": sum(len(p) for p in emptied.values()),
        "freed_bytes": freed,
        "freed_formatted": format_size(freed),
        "errors": errors,
    }
