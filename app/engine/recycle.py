"""Recycle Bin / Trash helpers: drives without one, JunkZero's holding folder, and restoring.

USB sticks, memory cards and network shares have no Recycle Bin, and Windows erases items
there for good when asked to recycle them quietly. JunkZero never sends anything there:
on such drives it moves items into a hidden holding folder on the same drive instead
(<drive>/.JunkZero-holding/<when>/...), keeps them for HOLDING_DAYS days, then removes them.

Restoring puts items back where they were, from the holding folder or from the system's
Recycle Bin / Trash, and never overwrites something that is already at that location.
"""
from __future__ import annotations
import errno
import os
import re
import shutil
import struct
import subprocess
import time
import uuid
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import unquote

from app.config import HOLDING_DAYS, HOLDING_DIR_NAME
from app.engine import osinfo, storage

# ---------------------------------------------------------------- Drives without a Recycle Bin

_DRIVE_REMOVABLE, _DRIVE_FIXED, _DRIVE_REMOTE, _DRIVE_CDROM, _DRIVE_RAMDISK = 2, 3, 4, 5, 6
_MAC_NETWORK_FS = {"smbfs", "afpfs", "nfs", "webdav", "ftp", "cifs"}
# File systems whose Trash macOS keeps reliably. On others (exFAT, FAT, NTFS, and the FSKit
# drivers macOS uses for them), "move to Trash" can erase the item at once instead.
_MAC_TRASH_FS = {"apfs", "hfs"}
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


# While a delete runs: drive root -> what was looked up for it, so a cleanup of thousands of
# items asks Windows about each drive once instead of once per item
_drive_cache: Optional[Dict[Tuple[str, str], Any]] = None


@contextmanager
def per_drive_lookups():
    """Look up each drive's type and Recycle Bin settings once while this is open."""
    global _drive_cache
    outer = _drive_cache
    if outer is None:
        _drive_cache = {}
    try:
        yield
    finally:
        _drive_cache = outer


def _cached(kind: str, root: str, lookup):
    cache = _drive_cache
    if cache is None:
        return lookup()
    key = (kind, root)
    if key not in cache:
        cache[key] = lookup()
    return cache[key]


def has_recycle_bin(path: str) -> bool:
    """False for USB sticks, memory cards, CDs and network shares, where a "recycled" item is
    erased for good. Fixed disks (and, on Windows, anything that can't be told) count as having
    one. On a Mac only the startup disk and APFS or Mac OS Extended disks do: an exFAT or FAT
    disk, or one whose file system can't be told, gets JunkZero's holding folder instead."""
    if osinfo.is_windows():
        if os.path.abspath(path).startswith("\\\\"):
            return False  # \\server\share
        root = mount_point(path)
        try:
            kind = _cached("type", root, lambda: _windows_drive_type(root))
        except (OSError, AttributeError, ValueError):
            return True
        return kind not in (_DRIVE_REMOVABLE, _DRIVE_REMOTE, _DRIVE_CDROM, _DRIVE_RAMDISK)
    if osinfo.is_macos():
        root = mount_point(path)
        fs = (_mac_mounts().get(root) or "").lower()
        if fs in _MAC_NETWORK_FS:
            return False
        return root == "/" or fs in _MAC_TRASH_FS
    return True


_MB = 1024 * 1024
_BITBUCKET_KEY = r"Software\Microsoft\Windows\CurrentVersion\Explorer\BitBucket\Volume"


def _windows_recycle_settings(root: str) -> Tuple[Optional[int], bool]:
    """(Recycle Bin size limit in bytes or None if unknown, erase-instead-of-recycle switch) for
    the drive, as set in the Recycle Bin's Properties."""
    import ctypes
    import winreg
    buf = ctypes.create_unicode_buffer(64)
    if not ctypes.windll.kernel32.GetVolumeNameForVolumeMountPointW(root, buf, len(buf)):
        return None, False
    guid = buf.value.rstrip("\\").rsplit("Volume", 1)[-1]  # "{...}"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _BITBUCKET_KEY + "\\" + guid) as key:
            def value(name: str) -> Optional[int]:
                try:
                    return int(winreg.QueryValueEx(key, name)[0])
                except (OSError, ValueError, TypeError):
                    return None
            limit_mb, nuke = value("MaxCapacity"), value("NukeOnDelete")
    except OSError:
        return None, False
    return (limit_mb * _MB if limit_mb else None), bool(nuke)


def recycle_bin_takes(path: str, size: int) -> bool:
    """True if sending path to the Recycle Bin / Trash really keeps it there. Windows quietly
    erases items larger than the Recycle Bin's size limit, and every item when the Recycle Bin
    is set to remove files immediately; those go to JunkZero's holding folder instead."""
    if not has_recycle_bin(path):
        return False
    if not osinfo.is_windows():
        return True
    root = mount_point(path)

    def settings() -> Tuple[Optional[int], bool]:
        try:
            limit, nuke = _windows_recycle_settings(root)
        except (OSError, AttributeError, ImportError, ValueError):
            limit, nuke = None, False
        if limit is None and not nuke:
            # Not set: Windows uses a share of the drive. Assume the smallest it picks (5%).
            try:
                limit = shutil.disk_usage(root).total // 20
            except OSError:
                limit = None
        return limit, nuke

    limit, nuke = _cached("settings", root, settings)
    if nuke:
        return False
    if limit is None:
        return True
    return size < limit


# ---------------------------------------------------------------- What a delete does on each disk

# Where a deleted item goes: the system's Recycle Bin / Trash, JunkZero's holding folder on the
# same disk (kept HOLDING_DAYS days), erased (permanent mode), or nowhere: the disk is read-only
DEST_TRASH, DEST_HOLDING, DEST_ERASE, DEST_READ_ONLY = "trash", "holding", "erase", "read_only"

_FS_LABELS = {
    "apfs": "APFS", "hfs": "Mac OS Extended", "exfat": "exFAT", "msdos": "FAT", "fat": "FAT",
    "fat32": "FAT32", "vfat": "FAT", "ntfs": "NTFS", "ufsd_ntfs": "NTFS", "tuxera_ntfs": "NTFS",
    "refs": "ReFS", "smbfs": "SMB network share", "afpfs": "AFP network share", "nfs": "NFS network share",
    "webdav": "WebDAV share", "cifs": "network share", "cd9660": "CD/DVD", "udf": "CD/DVD", "cdfs": "CD/DVD",
    "ext4": "ext4", "btrfs": "Btrfs", "xfs": "XFS",
}
_FILE_READ_ONLY_VOLUME = 0x00080000


def fs_label(fs: str) -> str:
    fs = (fs or "").strip()
    return _FS_LABELS.get(fs.lower(), fs.upper() if len(fs) <= 5 else fs)


def _windows_volume(root: str) -> Tuple[str, str, bool]:
    """(volume label, file system name, read-only) for a Windows drive root."""
    import ctypes
    name, fs = ctypes.create_unicode_buffer(261), ctypes.create_unicode_buffer(261)
    flags = ctypes.c_ulong(0)
    ok = ctypes.windll.kernel32.GetVolumeInformationW(root, name, 261, None, None, ctypes.byref(flags), fs, 261)
    if not ok:
        return "", "", False
    return name.value, fs.value, bool(flags.value & _FILE_READ_ONLY_VOLUME)


def is_read_only(path: str) -> bool:
    """True if nothing can be deleted where path is: a read-only disk (a CD, or an NTFS disk on a
    Mac, which macOS can only read)."""
    folder = os.path.dirname(os.path.abspath(path)) or path
    if osinfo.is_windows():
        try:
            root = mount_point(path)
            if _windows_drive_type(root) == _DRIVE_CDROM:
                return True
            return _windows_volume(root)[2]
        except (OSError, AttributeError, ValueError):
            return False
    try:
        # The folder holding the item, not the disk root: on a Mac "/" is the read-only system
        # volume while the files in /Users live on the writable data volume
        return bool(os.statvfs(folder).f_flag & os.ST_RDONLY)
    except (OSError, AttributeError):
        return False


def disk_info(path: str) -> Dict[str, Any]:
    """The disk holding path, and what deleting from it does (ignoring permanent mode and the
    Windows Recycle Bin's size limit, which depend on the item): name, file system, destination
    (DEST_TRASH, DEST_HOLDING or DEST_READ_ONLY) and, unless it is the Trash, why."""
    root = mount_point(path)
    name, fs = "", ""
    if osinfo.is_windows():
        try:
            name, fs, _ = _windows_volume(root)
        except (OSError, AttributeError, ValueError):
            pass
        name = f"{name} ({root.rstrip(chr(92))})" if name else root
    else:
        if osinfo.is_macos():
            fs = _mac_mounts().get(root) or ""
        name = ("Macintosh HD" if osinfo.is_macos() else "/") if root == "/" else os.path.basename(root.rstrip("/")) or root
    label = fs_label(fs)
    trash = "Trash" if not osinfo.is_windows() else "Recycle Bin"
    info = {"root": root, "name": name, "fs": fs, "fs_label": label, "destination": DEST_TRASH, "reason": ""}

    if is_read_only(path):
        info["destination"] = DEST_READ_ONLY
        if osinfo.is_macos() and fs.lower() in ("ntfs", "ufsd_ntfs", "tuxera_ntfs"):
            info["reason"] = "macOS can only read NTFS disks, so nothing on it can be deleted"
        else:
            info["reason"] = "the disk is read-only, so nothing on it can be deleted"
        return info
    if has_recycle_bin(path):
        return info

    info["destination"] = DEST_HOLDING
    if osinfo.is_windows():
        kind = None
        try:
            kind = _windows_drive_type(root)
        except (OSError, AttributeError, ValueError):
            pass
        if os.path.abspath(path).startswith("\\\\") or kind == _DRIVE_REMOTE:
            info["reason"] = f"network drives have no {trash}"
        elif kind == _DRIVE_RAMDISK:
            info["reason"] = f"RAM disks have no {trash}"
        else:
            info["reason"] = f"USB drives and memory cards have no {trash}"
    elif fs.lower() in _MAC_NETWORK_FS:
        info["reason"] = f"network drives have no {trash}"
    elif fs:
        info["reason"] = f"on {label} disks macOS may erase items instead of keeping them in the {trash}"
    else:
        info["reason"] = f"JunkZero can't tell how this disk is formatted, so it doesn't rely on the {trash}"
    return info


def item_destination(path: str, size: int, permanent: bool, info: Optional[Dict[str, Any]] = None) -> Tuple[str, str]:
    """(destination, why) for deleting one item: what the delete will really do with it."""
    info = info if info is not None else disk_info(path)
    if info["destination"] == DEST_READ_ONLY:
        return DEST_READ_ONLY, info["reason"]
    if permanent:
        return DEST_ERASE, ""
    if recycle_bin_takes(path, size):
        return DEST_TRASH, ""
    if info["destination"] == DEST_HOLDING and info["reason"]:
        return DEST_HOLDING, info["reason"]
    # Windows: bigger than this drive's Recycle Bin, or the Recycle Bin removes files at once
    return DEST_HOLDING, "the Recycle Bin on this drive would erase it (too big for it, or set to remove files immediately)"


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
    try:
        os.rename(path, dest)
    except OSError:
        # Don't leave empty folders behind in the holding folder for an item that stayed put
        _remove_empty_dirs(os.path.dirname(dest), stop_at=batch)
        raise
    return dest


def _remove_empty_dirs(folder: str, stop_at: str) -> None:
    """Remove folder and its parents while they are empty, up to (not including) stop_at."""
    stop = os.path.normcase(os.path.abspath(stop_at))
    folder = os.path.abspath(folder)
    while os.path.normcase(folder) != stop and os.path.normcase(folder).startswith(stop + os.sep.lower()):
        try:
            os.rmdir(folder)
        except OSError:
            return
        folder = os.path.dirname(folder)


def _batch_of(held_path: str) -> Optional[Tuple[str, str]]:
    """(batch folder, drive root) for a path inside a holding batch, or None."""
    parts = os.path.abspath(held_path).split(os.sep)
    for i, part in enumerate(parts):
        if part == HOLDING_DIR_NAME and i + 1 < len(parts) and _BATCH_RE.match(parts[i + 1]):
            root = os.sep.join(parts[:i]) or os.sep
            if re.fullmatch(r"[A-Za-z]:", root):
                root += os.sep
            return os.sep.join(parts[:i + 2]), root
    return None


def _sweep_batch(batch: str, root: str, restored: List[str], errors: List[Dict[str, str]]) -> None:
    """Put back everything still in one holding batch (each item's place is its path inside the
    batch, relative to the drive root), never over something already there."""
    for dirpath, dirnames, filenames in os.walk(batch, topdown=True):
        rel_dir = os.path.relpath(dirpath, batch)
        keep = []
        for name in dirnames:
            stored = os.path.join(dirpath, name)
            original = os.path.normpath(os.path.join(root, rel_dir, name))
            if os.path.isdir(original) and not os.path.islink(original):
                keep.append(name)  # the folder is back already: put back what's inside it
                continue
            try:
                _move_back(stored, original)
                restored.append(original)
            except _Occupied:
                errors.append({"path": original, "error": "Something is already at this location; left as it is"})
            except OSError as e:
                errors.append({"path": original, "error": str(e)})
        dirnames[:] = keep
        for name in filenames:
            original = os.path.normpath(os.path.join(root, rel_dir, name))
            try:
                _move_back(os.path.join(dirpath, name), original)
                restored.append(original)
            except _Occupied:
                errors.append({"path": original, "error": "Something is already at this location; left as it is"})
            except OSError as e:
                errors.append({"path": original, "error": str(e)})
    # What's left is empty folders that are back in place already
    for dirpath, _dirs, _files in sorted(os.walk(batch), key=lambda w: -len(w[0])):
        try:
            os.rmdir(dirpath)
        except OSError:
            pass


def _remember_holding_root(root: str) -> None:
    roots = storage.load_settings().get("holding_roots") or []
    if root not in roots:
        storage.update_settings(holding_roots=[*roots, root])


def purge_holding(now: Optional[float] = None, days: int = HOLDING_DAYS) -> int:
    """Remove holding batches older than `days` whose cleanup is recorded (in the history or the
    cleanup's own file) and was at least `days` ago; returns how many were removed. A batch no
    cleanup record mentions is never erased: nothing would say what it held. Drives that
    aren't plugged in are kept in the list for next time."""
    now = time.time() if now is None else now
    removed = 0
    keep_roots = []
    recorded = storage.recorded_holding_batches()
    purged: List[str] = []
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
            recorded_at = recorded.get(entry.name)
            if recorded_at is None:
                continue
            if now - made >= days * 86400 and now - recorded_at >= days * 86400:
                shutil.rmtree(entry.path, ignore_errors=True)
                if not os.path.exists(entry.path):
                    purged.append(entry.name)
                removed += 1
        try:
            os.rmdir(holding)  # only succeeds once nothing is left in it
        except OSError:
            keep_roots.append(root)
    storage.update_settings(holding_roots=keep_roots)
    storage.forget_purged_cleanups(purged)
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


RecycleIndex = Dict[str, List[Tuple[float, str, str]]]


def windows_recycle_index(bin_root: str) -> RecycleIndex:
    """Every item in a drive's Recycle Bin, read once: original path (normalised) ->
    [(deleted at, stored item, its $I file)]."""
    index: RecycleIndex = {}
    try:
        user_bins = [e.path for e in os.scandir(bin_root) if e.is_dir(follow_symlinks=False)]
    except OSError:
        return index
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
            if not info:
                continue
            stored = os.path.join(user_bin, "$R" + entry.name[2:])
            index.setdefault(_norm(info[0]), []).append((info[1], stored, entry.path))
    return index


def _norm(path: str) -> str:
    return os.path.normcase(os.path.normpath(path))


def find_in_windows_recycle_bin(original: str, since: float, bin_root: Optional[str] = None,
                                index: Optional[RecycleIndex] = None) -> Optional[Tuple[str, List[str]]]:
    """(stored item, files to remove after restoring) for the newest matching item deleted after since."""
    if index is None:
        index = windows_recycle_index(bin_root or os.path.join(mount_point(original), "$Recycle.Bin"))
    best: Optional[Tuple[float, str, str]] = None
    for deleted, stored, info_file in index.get(_norm(original), []):
        if deleted < since:
            continue
        if os.path.lexists(stored) and (best is None or deleted > best[0]):
            best = (deleted, stored, info_file)
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


def find_in_mac_trash(original: str, since: float, trash: Optional[str] = None,
                      file_id: Optional[List[int]] = None, until: Optional[float] = None) -> Optional[Tuple[str, List[str]]]:
    """The Trash keeps no note of where an item came from, and Finder renames items that clash
    with something already there ("report 2.pdf"). An item moved into the Trash keeps its
    device and inode, so with file_id (recorded when JunkZero deleted it) only that very item
    matches, whatever its name now. Without it, only a single item with exactly the same name,
    moved there during the cleanup, is trusted; anything less certain is not restored."""
    if trash is None:
        home = os.path.expanduser("~")
        top = mount_point(original)
        trash = os.path.join(home, ".Trash") if top == mount_point(home) else \
            os.path.join(top, ".Trashes", str(os.getuid()))
    name = os.path.basename(original.rstrip("/"))
    try:
        entries = list(os.scandir(trash))
    except OSError:
        return None
    if file_id:
        for entry in entries:
            try:
                st = os.lstat(entry.path)  # scandir's own stat has no inode on Windows
            except OSError:
                continue
            if [st.st_dev, st.st_ino] == list(file_id):
                return entry.path, []
        return None
    matches = []
    for entry in entries:
        if entry.name != name:
            continue
        try:
            moved = entry.stat(follow_symlinks=False).st_ctime  # Changes when moved into the Trash
        except OSError:
            continue
        if moved >= since - 1 and (until is None or moved <= until):
            matches.append(entry.path)
    return (matches[0], []) if len(matches) == 1 else None


def find_in_trash(original: str, since: float, file_id: Optional[List[int]] = None,
                  until: Optional[float] = None, bins: Optional[Dict[str, RecycleIndex]] = None
                  ) -> Optional[Tuple[str, List[str]]]:
    """bins: Recycle Bins already read during this restore (bin folder -> index), filled as needed."""
    if osinfo.is_windows():
        bin_root = os.path.join(mount_point(original), "$Recycle.Bin")
        if bins is None:
            return find_in_windows_recycle_bin(original, since, bin_root)
        if bin_root not in bins:
            bins[bin_root] = windows_recycle_index(bin_root)
        return find_in_windows_recycle_bin(original, since, index=bins[bin_root])
    if osinfo.is_macos():
        return find_in_mac_trash(original, since, file_id=file_id, until=until)
    return find_in_xdg_trash(original, since)


# ---------------------------------------------------------------- Restoring a cleanup

class _Occupied(OSError):
    pass


def _move_back(stored: str, original: str) -> None:
    """Move stored back to original, never over something that is already there."""
    os.makedirs(os.path.dirname(original) or ".", exist_ok=True)
    if os.path.lexists(original):
        raise _Occupied()
    try:
        os.rename(stored, original)
    except FileExistsError as e:
        raise _Occupied() from e
    except OSError as e:
        if e.errno != errno.EXDEV:
            raise
        # Different drive: copy across, still only to an empty spot
        if os.path.lexists(original):
            raise _Occupied() from e
        shutil.move(stored, original)


def restore_cleanup(record: Dict[str, Any]) -> Dict[str, Any]:
    """Put back the items of one cleanup history record. Returns counts and per-item errors."""
    restored: List[str] = []
    errors: List[Dict[str, str]] = []
    if record.get("mode") == "permanent":
        return {"restored": [], "errors": [{"path": "", "error": "Permanently deleted items can't be restored"}]}
    since = float(record.get("started_at") or record.get("timestamp", 0)) - 120
    until = float(record.get("timestamp") or since) + 120
    held = record.get("held") or {}
    trash_ids = record.get("trash_ids") or {}
    already = set(record.get("restored") or [])
    bins: Dict[str, RecycleIndex] = {}  # Each Recycle Bin is read once for the whole restore
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
                found = find_in_trash(original, since, trash_ids.get(original), until, bins)
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
        except _Occupied:
            errors.append({"path": original, "error": "Something is already at this location; left as it is"})
        except OSError as e:
            errors.append({"path": original, "error": str(e)})

    # Anything still in this cleanup's holding batches goes back too. Older history kept only
    # the first 500 items of a cleanup, but the batch folder holds all of them.
    batches = {b for b in (_batch_of(d) for d in held.values() if isinstance(d, str)) if b}
    for batch, root in sorted(batches):
        if os.path.isdir(batch):
            _sweep_batch(batch, root, restored, errors)
    return {"restored": restored, "errors": errors}
