"""Recycle Bin / Trash helpers: drives without one, JunkZero's holding folder, and restoring.

USB sticks, memory cards and network shares have no Recycle Bin, and Windows erases items
there for good when asked to recycle them quietly. JunkZero never sends anything there:
on such drives it moves items into a hidden holding folder on the same drive instead
(<drive>/.JunkZero-holding/<when>/...), keeps them for HOLDING_DAYS days, then removes them.

Restoring puts items back where they were, from the holding folder or from the system's
Recycle Bin / Trash, and never overwrites something that is already at that location.
"""
from __future__ import annotations
import os
import re
import shutil
import struct
import subprocess
import time
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import unquote

from app.config import HOLDING_DAYS, HOLDING_DIR_NAME
from app.engine import osinfo, storage

# ---------------------------------------------------------------- Drives without a Recycle Bin

_DRIVE_REMOVABLE, _DRIVE_FIXED, _DRIVE_REMOTE, _DRIVE_CDROM, _DRIVE_RAMDISK = 2, 3, 4, 5, 6
_MAC_NETWORK_FS = {"smbfs", "afpfs", "nfs", "webdav", "ftp", "cifs"}
_mac_mounts_cache: Tuple[float, Dict[str, str]] = (0.0, {})


def _windows_drive_type(root: str) -> int:
    import ctypes
    return ctypes.windll.kernel32.GetDriveTypeW(root)


def _mac_mounts() -> Dict[str, str]:
    """Mount point -> file system type, from `mount` (cached for a minute)."""
    global _mac_mounts_cache
    when, mounts = _mac_mounts_cache
    if time.time() - when < 60:
        return mounts
    mounts = {}
    try:
        out = subprocess.run(["mount"], capture_output=True, text=True, timeout=10).stdout
        for line in out.splitlines():
            m = re.match(r"^.+? on (.+) \(([^,)]+)", line)
            if m:
                mounts[m.group(1)] = m.group(2).strip()
    except (OSError, subprocess.SubprocessError):
        pass
    _mac_mounts_cache = (time.time(), mounts)
    return mounts


def mount_point(path: str) -> str:
    """The drive root (Windows: "E:\\" or "\\\\server\\share\\") or mount point holding path."""
    path = os.path.abspath(path)
    if osinfo.is_windows():
        drive = os.path.splitdrive(path)[0]
        return drive.rstrip("\\/") + "\\"
    while not os.path.ismount(path):
        parent = os.path.dirname(path)
        if parent == path:
            break
        path = parent
    return path


def has_recycle_bin(path: str) -> bool:
    """False for USB sticks, memory cards, CDs and network shares, where a "recycled" item is
    erased for good. Fixed disks (and anything that can't be told) count as having one."""
    if osinfo.is_windows():
        if os.path.abspath(path).startswith("\\\\"):
            return False  # \\server\share
        try:
            kind = _windows_drive_type(mount_point(path))
        except (OSError, AttributeError, ValueError):
            return True
        return kind not in (_DRIVE_REMOVABLE, _DRIVE_REMOTE, _DRIVE_CDROM, _DRIVE_RAMDISK)
    if osinfo.is_macos():
        return _mac_mounts().get(mount_point(path), "apfs") not in _MAC_NETWORK_FS
    return True


# ---------------------------------------------------------------- Holding folder

_BATCH_RE = re.compile(r"^(\d{8}-\d{6})-[0-9a-f]{6}$")


def _hide_on_windows(path: str) -> None:
    if osinfo.is_windows():
        try:
            import ctypes
            ctypes.windll.kernel32.SetFileAttributesW(path, 0x2)  # FILE_ATTRIBUTE_HIDDEN
        except (OSError, AttributeError):
            pass


def new_holding_batch(root: str, now: Optional[float] = None) -> str:
    """A fresh folder for one cleanup inside <root>/.JunkZero-holding."""
    now = time.time() if now is None else now
    holding = os.path.join(root, HOLDING_DIR_NAME)
    os.makedirs(holding, exist_ok=True)
    _hide_on_windows(holding)
    batch = os.path.join(holding, datetime.fromtimestamp(now).strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6])
    os.makedirs(batch)
    _remember_holding_root(root)
    return batch


def move_to_holding(path: str, batch: str) -> str:
    """Move path into the batch folder, keeping its folders, and return where it went."""
    path = os.path.abspath(path)
    root = mount_point(path)
    rel = os.path.relpath(path, root)
    dest = os.path.join(batch, rel)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    os.rename(path, dest)
    return dest


def _remember_holding_root(root: str) -> None:
    roots = storage.load_settings().get("holding_roots") or []
    if root not in roots:
        storage.update_settings(holding_roots=[*roots, root])


def purge_holding(now: Optional[float] = None, days: int = HOLDING_DAYS) -> int:
    """Remove holding batches older than `days`; returns how many were removed. Drives that
    aren't plugged in are kept in the list for next time."""
    now = time.time() if now is None else now
    removed = 0
    keep_roots = []
    for root in storage.load_settings().get("holding_roots") or []:
        holding = os.path.join(root, HOLDING_DIR_NAME)
        if not os.path.isdir(root):
            keep_roots.append(root)
            continue
        if not os.path.isdir(holding):
            continue
        for entry in list(os.scandir(holding)):
            m = _BATCH_RE.match(entry.name)
            if not m or not entry.is_dir(follow_symlinks=False):
                continue
            made = time.mktime(datetime.strptime(m.group(1), "%Y%m%d-%H%M%S").timetuple())
            if now - made >= days * 86400:
                shutil.rmtree(entry.path, ignore_errors=True)
                removed += 1
        try:
            os.rmdir(holding)  # only succeeds once nothing is left in it
        except OSError:
            keep_roots.append(root)
    storage.update_settings(holding_roots=keep_roots)
    return removed


# ---------------------------------------------------------------- Finding items in the Recycle Bin / Trash

_FILETIME_EPOCH = 11644473600


def parse_recycle_info(data: bytes) -> Optional[Tuple[str, float]]:
    """(original path, deleted at) from a Windows Recycle Bin $I file (Vista and later)."""
    if len(data) < 24:
        return None
    version, _size, filetime = struct.unpack_from("<qqq", data, 0)
    if version == 1:
        raw = data[24:24 + 520]
    elif version == 2 and len(data) >= 28:
        (chars,) = struct.unpack_from("<i", data, 24)
        raw = data[28:28 + chars * 2]
    else:
        return None
    path = raw.decode("utf-16-le", errors="replace").split("\0", 1)[0]
    return (path, filetime / 1e7 - _FILETIME_EPOCH) if path else None


def _same(a: str, b: str) -> bool:
    return os.path.normcase(os.path.normpath(a)) == os.path.normcase(os.path.normpath(b))


def find_in_windows_recycle_bin(original: str, since: float, bin_root: Optional[str] = None) -> Optional[Tuple[str, List[str]]]:
    """(stored item, files to remove after restoring) for the newest matching item deleted after since."""
    bin_root = bin_root or os.path.join(mount_point(original), "$Recycle.Bin")
    best: Optional[Tuple[float, str, str]] = None
    try:
        user_bins = [e.path for e in os.scandir(bin_root) if e.is_dir(follow_symlinks=False)]
    except OSError:
        return None
    for user_bin in user_bins:
        try:
            entries = list(os.scandir(user_bin))  # Only the current user's own folder can be read
        except OSError:
            continue
        for entry in entries:
            if not entry.name.upper().startswith("$I"):
                continue
            try:
                with open(entry.path, "rb") as f:
                    info = parse_recycle_info(f.read(4096))
            except OSError:
                continue
            if not info or not _same(info[0], original) or info[1] < since:
                continue
            stored = os.path.join(user_bin, "$R" + entry.name[2:])
            if os.path.lexists(stored) and (best is None or info[1] > best[0]):
                best = (info[1], stored, entry.path)
    return (best[1], [best[2]]) if best else None


def _xdg_trash_dirs(original: str) -> List[Tuple[str, str]]:
    """(trash folder, topdir paths are relative to, or "") for the freedesktop.org Trash."""
    home_trash = os.path.join(os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share"), "Trash")
    dirs = [(home_trash, "")]
    top = mount_point(original)
    uid = os.getuid() if hasattr(os, "getuid") else 0
    dirs += [(os.path.join(top, ".Trash", str(uid)), top), (os.path.join(top, f".Trash-{uid}"), top)]
    return dirs


def find_in_xdg_trash(original: str, since: float, trash_dirs: Optional[List[Tuple[str, str]]] = None) -> Optional[Tuple[str, List[str]]]:
    best: Optional[Tuple[float, str, str]] = None
    for trash, top in (trash_dirs if trash_dirs is not None else _xdg_trash_dirs(original)):
        info_dir = os.path.join(trash, "info")
        try:
            entries = list(os.scandir(info_dir))
        except OSError:
            continue
        for entry in entries:
            if not entry.name.endswith(".trashinfo"):
                continue
            try:
                with open(entry.path, encoding="utf-8") as f:
                    text = f.read(8192)
            except (OSError, UnicodeDecodeError):
                continue
            path_m = re.search(r"^Path=(.+)$", text, re.M)
            date_m = re.search(r"^DeletionDate=(\S+)$", text, re.M)
            if not path_m:
                continue
            path = unquote(path_m.group(1).strip())
            if top and not os.path.isabs(path):
                path = os.path.join(top, path)
            try:
                deleted = time.mktime(datetime.strptime(date_m.group(1), "%Y-%m-%dT%H:%M:%S").timetuple()) if date_m else 0
            except ValueError:
                deleted = 0
            if not _same(path, original) or deleted < since - 1:
                continue
            stored = os.path.join(trash, "files", entry.name[:-len(".trashinfo")])
            if os.path.lexists(stored) and (best is None or deleted > best[0]):
                best = (deleted, stored, entry.path)
    return (best[1], [best[2]]) if best else None


def find_in_mac_trash(original: str, since: float, trash: Optional[str] = None) -> Optional[Tuple[str, List[str]]]:
    """Finder renames items that clash with something already in the Trash ("report 2.pdf",
    "report 10.21.33 AM.pdf"), so match the name or the name with words added before the extension."""
    if trash is None:
        home = os.path.expanduser("~")
        top = mount_point(original)
        trash = os.path.join(home, ".Trash") if top == mount_point(home) else \
            os.path.join(top, ".Trashes", str(os.getuid()))
    name = os.path.basename(original.rstrip("/"))
    stem, ext = os.path.splitext(name)
    best: Optional[Tuple[int, float, str]] = None
    try:
        entries = list(os.scandir(trash))
    except OSError:
        return None
    for entry in entries:
        exact = entry.name == name
        if not exact and not (entry.name.startswith(stem + " ") and entry.name.endswith(ext)):
            continue
        try:
            moved = entry.stat(follow_symlinks=False).st_ctime  # Changes when moved into the Trash
        except OSError:
            continue
        if moved < since - 1:
            continue
        key = (1 if exact else 0, moved, entry.path)
        if best is None or key[:2] > best[:2]:
            best = key
    return (best[2], []) if best else None


def find_in_trash(original: str, since: float) -> Optional[Tuple[str, List[str]]]:
    if osinfo.is_windows():
        return find_in_windows_recycle_bin(original, since)
    if osinfo.is_macos():
        return find_in_mac_trash(original, since)
    return find_in_xdg_trash(original, since)


# ---------------------------------------------------------------- Restoring a cleanup

def _move_back(stored: str, original: str) -> None:
    os.makedirs(os.path.dirname(original) or ".", exist_ok=True)
    try:
        os.rename(stored, original)
    except OSError:
        shutil.move(stored, original)  # Different drive


def restore_cleanup(record: Dict[str, Any]) -> Dict[str, Any]:
    """Put back the items of one cleanup history record. Returns counts and per-item errors."""
    restored: List[str] = []
    errors: List[Dict[str, str]] = []
    if record.get("mode") == "permanent":
        return {"restored": [], "errors": [{"path": "", "error": "Permanently deleted items can't be restored"}]}
    since = float(record.get("started_at") or record.get("timestamp", 0)) - 120
    held = record.get("held") or {}
    already = set(record.get("restored") or [])
    for original in record.get("paths") or []:
        if original in already:
            continue
        if os.path.lexists(original):
            errors.append({"path": original, "error": "Something is already at this location; left as it is"})
            continue
        try:
            if original in held:
                stored = held[original]
                if not os.path.lexists(stored):
                    errors.append({"path": original, "error": "No longer in JunkZero's holding folder"})
                    continue
                _move_back(stored, original)
            else:
                found = find_in_trash(original, since)
                if not found:
                    errors.append({"path": original, "error": "Not found in the Recycle Bin (it may have been emptied)"})
                    continue
                stored, leftovers = found
                _move_back(stored, original)
                for extra in leftovers:
                    try:
                        os.remove(extra)
                    except OSError:
                        pass
            restored.append(original)
        except OSError as e:
            errors.append({"path": original, "error": str(e)})
    return {"restored": restored, "errors": errors}
