"""Safe and audited deletion engine with Recycle Bin integration."""
from __future__ import annotations
import hashlib
import os
import shutil
import sys
import logging
import logging.handlers
import threading
import uuid
from datetime import datetime
from pathlib import Path
import time
from typing import Any, Dict, List, Optional, Tuple
from pydantic import BaseModel, Field
import send2trash

from app.config import (
    BACKUP_DIR_NAMES, BACKUP_DIR_SUFFIXES, CAT_DUPLICATE_FOLDERS, CAT_DUPLICATES, CAT_EMPTY_FOLDERS, HOLDING_DIR_NAME, VCS_DIR_NAMES,
)
from app.engine.classifier import is_system_protected_path, format_size
from app.engine.exclusions import ExclusionMatcher
from app.engine.inuse import OpenFileCheck
from app.engine import osinfo
from app.engine.osinfo import is_hidden
from app.engine import dupfolders, permissions, recycle, storage
from app.engine.locations import is_protected_user_folder, protected_user_folders

# Kept with the rest of JunkZero's data (%APPDATA%\JunkZero), not in whatever folder the app started from
AUDIT_LOG_FILE = storage.data_dir() / "cleaner_audit.log"

# Setup dedicated cleaner logger
logger = logging.getLogger("JunkZeroCleaner")
logger.setLevel(logging.INFO)
if not logger.handlers:
    # Rotated (1 MB x 3) so it never grows without end
    handler = logging.handlers.RotatingFileHandler(AUDIT_LOG_FILE, maxBytes=1024 * 1024, backupCount=3,
                                                   encoding="utf-8", delay=True)
    handler.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s: %(message)s"))
    logger.addHandler(handler)


def folder_has_files(dir_path: str) -> bool:
    """Return True if the folder contains anything other than plain (empty) subfolders, at any depth.

    Files count whether hidden or not, and so does any hidden subfolder, even an empty one."""
    for root, dirs, files in os.walk(dir_path, onerror=_raise):
        if files:
            return True
        for d in dirs:
            path = os.path.join(root, d)
            if os.path.islink(path) or is_hidden(d, os.lstat(path)):
                return True
    return False


def in_vcs_folder(path: str) -> bool:
    """True if the path is a .git/.svn/.hg/.bzr folder or lies inside one."""
    parts = os.path.normpath(path).replace("\\", "/").lower().split("/")
    return any(part in VCS_DIR_NAMES for part in parts)


def in_backup_folder(path: str) -> bool:
    """True inside a backup folder (File History, Time Machine...) or JunkZero's holding folder."""
    parts = os.path.normpath(path).replace("\\", "/").split("/")
    return any(
        p.lower() in BACKUP_DIR_NAMES or p.lower().endswith(BACKUP_DIR_SUFFIXES) or p == HOLDING_DIR_NAME
        for p in parts
    )


def _key(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


def _gone(path: str, deleted_keys: set) -> bool:
    """True if path, or a folder holding it, was deleted earlier in this delete."""
    key = _key(path)
    while True:
        if key in deleted_keys:
            return True
        parent = os.path.dirname(key)
        if parent == key:
            return False
        key = parent


# One delete at a time: two at once could each see the other's copy of a duplicate as still
# there and remove both
_delete_lock = threading.Lock()

# Items handed to the Recycle Bin per call: one call per item is slow for thousands of items
TRASH_BATCH_SIZE = 100
# What the delete running now has done, for the progress bar (GET /api/clean/progress)
_progress: Dict[str, Any] = {"running": False, "done": 0, "total": 0}
_progress_lock = threading.Lock()
# Set by Cancel (POST /api/clean/cancel): the delete stops after the batch it is on
_cancel = threading.Event()


def delete_progress() -> Dict[str, Any]:
    """{"running", "done", "total"} for the delete running now (or the last one)."""
    with _progress_lock:
        return dict(_progress)


def delete_running() -> bool:
    with _progress_lock:
        return bool(_progress["running"])


def cancel_delete() -> bool:
    """Ask the running delete to stop after its current batch. False if none is running."""
    if not delete_running():
        return False
    _cancel.set()
    return True


def _set_progress(**values: Any) -> None:
    with _progress_lock:
        _progress.update(values)


_SAMPLE = 64 * 1024
HashCache = Dict[Tuple[str, int, int], Optional[str]]


def _sample(path: str, size: int) -> bytes:
    """The first and last 64 KB of a file."""
    with open(path, "rb") as f:
        head = f.read(_SAMPLE)
        if size > 2 * _SAMPLE:
            f.seek(-_SAMPLE, os.SEEK_END)
        return head + f.read(_SAMPLE)


def _full_hash(path: str, cache: HashCache) -> Optional[str]:
    """SHA-256 of a whole file, worked out once per file (and its size and modified time) per delete."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    key = (_key(path), st.st_size, st.st_mtime_ns)
    if key not in cache:
        digest = hashlib.sha256()
        try:
            with open(path, "rb") as f:
                for chunk in iter(lambda: f.read(1024 * 1024), b""):
                    digest.update(chunk)
            cache[key] = digest.hexdigest()
        except OSError:
            cache[key] = None
    return cache[key]


# Whole-file hashes worked out during the delete running now (it holds _delete_lock), so the
# copy that is kept is read once for all of its duplicates
_hashes: Optional[HashCache] = None


def same_content(a: str, b: str) -> bool:
    """True if both files exist and are byte-for-byte identical (checked again right before
    deleting). Size and the first and last 64 KB are compared first; only then are both read in
    full."""
    cache = _hashes if _hashes is not None else {}
    try:
        if not (os.path.isfile(a) and os.path.isfile(b)):
            return False
        if os.path.samefile(a, b):
            return False
        size = os.path.getsize(a)
        if size != os.path.getsize(b):
            return False
        if _sample(a, size) != _sample(b, size):
            return False
    except OSError:
        return False
    if size <= 2 * _SAMPLE:
        return True  # The samples were the whole files
    first, second = _full_hash(a, cache), _full_hash(b, cache)
    return first is not None and first == second


def _walk_folder(path: str, excluder: Optional[ExclusionMatcher] = None) -> Tuple[int, Optional[str], Optional[str]]:
    """(total size of the files inside, name of a version-control or backup folder found inside,
    an excluded file or folder found inside)."""
    size = 0
    # A path rule for something inside this folder: no need to walk to find it
    if excluder:
        inside = _excluded_path_inside(path, excluder)
        if inside:
            return size, None, inside
    check_patterns = bool(excluder and excluder.patterns)
    for root, dirs, files in os.walk(path):
        for d in dirs:
            if d.lower() in VCS_DIR_NAMES or in_backup_folder(d):
                return size, d, None
            if check_patterns and excluder.matches(os.path.join(root, d), d):
                return size, None, os.path.join(root, d)
        dirs[:] = [d for d in dirs if not os.path.islink(os.path.join(root, d))]
        for f in files:
            if check_patterns and excluder.matches(os.path.join(root, f), f):
                return size, None, os.path.join(root, f)
            try:
                size += os.lstat(os.path.join(root, f)).st_size
            except OSError:
                pass
    return size, None, None


def _excluded_path_inside(folder: str, excluder: ExclusionMatcher) -> Optional[str]:
    """An excluded file or folder (a path rule, as written) that exists inside folder."""
    for rule in excluder.paths_inside(folder):
        if os.path.lexists(rule):
            return rule
    return None


def _remove_permanently(path: str, unlock: bool = False) -> None:
    if not os.path.isdir(path):
        # A read-only file of the user's own (Windows read-only attribute, also shown as Locked
        # on FAT, exFAT, NTFS and network disks on a Mac), or one in their own read-only folder
        permissions.with_retry(lambda: os.remove(path), path, unlock)
        return
    if osinfo.is_windows():
        # Windows can't rename a folder while a file inside it is open, so this finds out
        # before anything is erased instead of stopping halfway through
        aside = f"{path.rstrip(os.sep)}.junkzero-{uuid.uuid4().hex[:6]}"
        try:
            os.rename(path, aside)
        except PermissionError as ex:
            if not permissions.make_writable(path, unlock):
                raise OSError("a file inside it is open in another program, or Windows denied access "
                              "(close programs using it and try again)") from ex
            try:
                os.rename(path, aside)
            except PermissionError as again:
                raise OSError("a file inside it is open in another program, or Windows denied access "
                              "(close programs using it and try again)") from again
        path = aside
    # Read-only files and folders of the user's own inside are made writable as they are reached
    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=lambda func, p, exc: permissions.retry_writable(func, p, exc, unlock))
    else:
        shutil.rmtree(path, onerror=lambda func, p, exc_info: permissions.retry_writable(func, p, exc_info[1], unlock))


class TrashSetupError(OSError):
    """The Trash can't work because of how JunkZero was installed (not a permission problem)."""


def _send_to_trash(path) -> None:
    """Send one path, or a list of paths in one call, to the Recycle Bin / Trash."""
    try:
        send2trash.send2trash(path)
    except OSError as ex:
        if osinfo.is_macos() and not osinfo.mac_trash_ready():
            # The old Trash API send2trash falls back to without pyobjc: say how to fix it
            raise TrashSetupError(f"macOS refused to move it to the Trash ({ex}). JunkZero is missing the pyobjc "
                                  f"package. {osinfo.MAC_TRASH_FIX}") from ex
        raise


def _raise(err: OSError) -> None:
    raise err


class CleanResult(BaseModel):
    total_requested: int
    deleted_count: int
    failed_count: int
    freed_bytes: int
    freed_formatted: str
    mode: str  # "recycle_bin" or "permanent"
    errors: List[Dict[str, str]] = Field(default_factory=list)
    # Items on drives without a Recycle Bin: original path -> where it was set aside
    held: Dict[str, str] = Field(default_factory=dict)
    # macOS: [device, inode] of each item sent to the Trash, so Restore finds that very item
    trash_ids: Dict[str, List[int]] = Field(default_factory=dict)
    started_at: float = 0.0
    # Not looked at because the delete was cancelled; nothing was done to them
    skipped_count: int = 0
    cancelled: bool = False


def delete_items(
    items: List[Dict[str, Any]],
    permanent: bool = False,
    open_check: Optional[OpenFileCheck] = None,
    exclusions: Optional[List[str]] = None,
    unlock: bool = False,
) -> CleanResult:
    """
    Delete a batch of files and directories. Items go to the Recycle Bin by default (or, on
    drives without one, to JunkZero's holding folder on that drive); pass permanent=True to
    erase them from disk directly.
    """
    global _hashes
    with _delete_lock:
        _hashes = {}
        _cancel.clear()
        _set_progress(running=True, done=0, total=len(items))
        try:
            # The user's exclusions (the list under Exclusions) are never deleted, from any screen
            excluder = ExclusionMatcher(storage.get_exclusions() if exclusions is None else exclusions)
            with recycle.per_drive_lookups():
                return _delete_items(items, permanent, open_check, excluder, unlock)
        finally:
            _set_progress(running=False)
            _cancel.clear()
            _hashes = None


def _delete_items(items: List[Dict[str, Any]], permanent: bool, open_check: Optional[OpenFileCheck],
                  excluder: Optional[ExclusionMatcher] = None, unlock: bool = False) -> CleanResult:
    """unlock: the user chose Unlock and delete, so their own Finder-locked items are unlocked first."""
    started_at = time.time()
    open_check = OpenFileCheck() if open_check is None else open_check
    deleted_keys = set()
    held: Dict[str, str] = {}
    trash_ids: Dict[str, List[int]] = {}
    batches: Dict[str, str] = {}
    deleted_count = 0
    failed_count = 0
    freed_bytes = 0
    errors: List[Dict[str, str]] = []
    mode_str = "permanent" if permanent else "recycle_bin"
    user_folders = protected_user_folders()
    # Checked and waiting to go to the Recycle Bin together: (path as asked, path, size)
    pending: List[Tuple[str, str, int]] = []
    skipped = 0
    disks: Dict[str, Dict[str, Any]] = {}

    def flush() -> None:
        """Send the waiting items to the Recycle Bin in one call; if that fails, the ones still
        in place are tried one by one so a single bad item doesn't fail the rest."""
        nonlocal deleted_count, failed_count, freed_bytes
        if not pending:
            return
        batch = list(pending)
        pending.clear()
        failures: Dict[str, Exception] = {}
        try:
            if len(batch) == 1:
                norm = batch[0][1]
                permissions.with_retry(lambda: _send_to_trash(norm), norm, unlock)
            else:
                _send_to_trash([p for _, p, _ in batch])
        except Exception as ex:
            if len(batch) == 1:
                failures[batch[0][1]] = ex
            else:
                for _, norm, _ in batch:
                    if not os.path.lexists(norm):
                        continue  # Moved before the call failed
                    try:
                        # One at a time, clearing a read-only attribute if that is what stops it
                        permissions.with_retry(lambda: _send_to_trash(norm), norm, unlock)
                    except Exception as one:
                        failures[norm] = one
        for asked, norm, size in batch:
            if norm in failures:
                failed_count += 1
                ex = failures[norm]
                error = {"path": asked, "error": str(ex)}
                # A permission problem: say why in plain words and what fixes it
                explained = None if isinstance(ex, TrashSetupError) else permissions.explain(ex, norm)
                if explained:
                    error["kind"], why = explained
                    error["error"] = f"Can't delete: {why}"
                errors.append(error)
                trash_ids.pop(asked, None)
                deleted_keys.discard(_key(norm))
                logger.error(f"Failed to delete {norm}: {ex}")
            else:
                deleted_count += 1
                freed_bytes += size
                logger.info(f"RecycleBin: {norm} ({format_size(size)})")

    for index, item in enumerate(items):
        if _cancel.is_set():
            flush()  # Finish the batch it is on, then stop
            skipped = len(items) - index
            logger.info(f"Delete cancelled: {skipped} item(s) left untouched")
            break
        _set_progress(done=index - len(pending))
        path_str = item.get("path", "")
        size = item.get("size_bytes", 0)

        if not isinstance(path_str, str) or not path_str or "\0" in path_str:
            errors.append({"path": str(path_str), "error": "Invalid or empty path"})
            failed_count += 1
            continue
        if not isinstance(size, int) or size < 0:
            errors.append({"path": path_str, "error": "Invalid size_bytes"})
            failed_count += 1
            continue

        # Critical Guardrail: NEVER delete protected system files
        if is_system_protected_path(path_str) or is_system_protected_path(os.path.realpath(path_str)):
            msg = "Deletion blocked: protected system path"
            errors.append({"path": path_str, "error": msg})
            logger.warning(f"Blocked attempt to delete system path: {path_str}")
            failed_count += 1
            continue

        # The user profile and its main personal folders (Documents, Desktop...) are never removed whole
        if is_protected_user_folder(path_str, folders=user_folders) or \
                is_protected_user_folder(os.path.realpath(path_str), folders=user_folders):
            msg = "Deletion blocked: personal folder (delete what's inside it instead)"
            errors.append({"path": path_str, "error": msg})
            logger.warning(f"Blocked attempt to delete personal folder: {path_str}")
            failed_count += 1
            continue

        # Version-control folders (.git, .svn...) and anything inside them are never deleted
        if in_vcs_folder(path_str):
            msg = "Deletion blocked: version-control folder (.git, .svn, .hg)"
            errors.append({"path": path_str, "error": msg})
            failed_count += 1
            continue

        # Excluded by the user: kept until they remove it from Exclusions
        rule = excluder.match(path_str) if excluder else None
        if rule is None and excluder and os.path.realpath(path_str) != path_str:
            rule = excluder.match(os.path.realpath(path_str))
        if rule is not None:
            errors.append({"path": path_str, "error": f"Excluded ({rule}): remove it from Exclusions to delete it"})
            failed_count += 1
            continue

        # Backups are a safety net, and the holding folder is JunkZero's own
        if in_backup_folder(path_str):
            errors.append({"path": path_str, "error": "Deletion blocked: backup folder"})
            failed_count += 1
            continue

        # Prevent root path deletion (e.g. C:\ or D:\)
        norm_path = os.path.abspath(path_str)
        resolved_path = Path(norm_path).resolve()
        if resolved_path == Path(resolved_path.anchor):
            msg = "Deletion blocked: root drive path"
            errors.append({"path": path_str, "error": msg})
            failed_count += 1
            continue

        if not path_str or not os.path.lexists(norm_path):
            errors.append({"path": path_str, "error": "Path does not exist"})
            failed_count += 1
            continue
        if os.path.islink(norm_path) or getattr(os.path, "isjunction", lambda _: False)(norm_path):
            errors.append({"path": path_str, "error": "Deletion blocked: symbolic link or junction"})
            failed_count += 1
            continue

        # A folder flagged as empty may have gained files since the scan
        if item.get("category") == CAT_EMPTY_FOLDERS:
            try:
                still_empty = os.path.isdir(norm_path) and not folder_has_files(norm_path)
            except OSError:
                still_empty = False
            if not still_empty:
                msg = "Deletion skipped: folder is no longer empty"
                errors.append({"path": path_str, "error": msg})
                logger.warning(f"Skipped empty-folder delete, folder now has content: {norm_path}")
                failed_count += 1
                continue

        # Never pull a file out from under a program that has it open
        in_use = open_check.reason(norm_path, os.path.isdir(norm_path))
        if in_use:
            errors.append({"path": path_str, "error": f"Skipped: {in_use} (close it and try again)"})
            failed_count += 1
            continue

        # A duplicate goes only while another identical copy is still there (and not deleted in this batch)
        if item.get("category") == CAT_DUPLICATES:
            others = [o for o in item.get("duplicate_paths") or [] if isinstance(o, str)]
            if not any(not _gone(o, deleted_keys) and same_content(o, norm_path) for o in others):
                msg = "Skipped: no other copy of this file is left, so this one is kept"
                errors.append({"path": path_str, "error": msg})
                logger.warning(f"Kept last copy: {norm_path}")
                failed_count += 1
                continue

        # A duplicate folder goes only while another folder with exactly the same files is still there
        if item.get("category") == CAT_DUPLICATE_FOLDERS:
            others = [o for o in item.get("duplicate_paths") or [] if isinstance(o, str)]
            if not any(not _gone(o, deleted_keys) and dupfolders.same_folder(o, norm_path, same_content)
                       for o in others):
                errors.append({"path": path_str, "error": "Skipped: no other folder with exactly the same files "
                               "is left, so this one is kept"})
                logger.warning(f"Kept last copy of folder: {norm_path}")
                failed_count += 1
                continue

        # A folder holding a .git (a build folder that is its own repository) or a backup is kept whole
        if os.path.isdir(norm_path):
            try:
                inside_size, kept, excluded_inside = _walk_folder(norm_path, excluder)
            except OSError:
                inside_size, kept, excluded_inside = 0, None, None
            if kept:
                errors.append({"path": path_str, "error": f"Deletion blocked: it holds a {kept} folder"})
                failed_count += 1
                continue
            if excluded_inside:
                errors.append({"path": path_str, "error": "Deletion blocked: it holds an excluded item "
                               f"({excluded_inside}); remove it from Exclusions or delete the rest separately"})
                failed_count += 1
                continue
            if size == 0:
                size = inside_size
        elif size == 0:
            try:
                size = os.path.getsize(norm_path)
            except OSError:
                pass

        # What this disk allows: the Trash, the holding folder, or nothing (read-only)
        root = recycle.mount_point(norm_path)
        if root not in disks:
            disks[root] = recycle.disk_info(norm_path)
        destination, why = recycle.item_destination(norm_path, size, permanent, disks[root])
        if destination == recycle.DEST_READ_ONLY:
            errors.append({"path": path_str, "error": f"Can't delete: {why} ({disks[root]['name']})",
                           "kind": permissions.KIND_READ_ONLY_DISK})
            failed_count += 1
            continue

        try:
            if destination == recycle.DEST_ERASE:
                # Permanent deletion direct from disk
                _remove_permanently(norm_path, unlock)
                logger.info(f"PermanentDelete: {norm_path} ({format_size(size)})")
            elif destination == recycle.DEST_TRASH:
                # Default: move to the Recycle Bin so the user can restore it
                if osinfo.is_macos():
                    st = os.lstat(norm_path)
                    trash_ids[path_str] = [st.st_dev, st.st_ino]
                # Goes with the rest of its batch; counts as gone for the duplicate check meanwhile
                pending.append((path_str, norm_path, size))
                deleted_keys.add(_key(norm_path))
                if len(pending) >= TRASH_BATCH_SIZE:
                    flush()
                continue
            else:
                # USB sticks and network shares have no Recycle Bin, and Windows erases items too big
                # for the Recycle Bin (or all items, when it is set to). Set it aside on the same
                # drive instead, where it can be restored for a week.
                root = recycle.holding_root(norm_path)
                try:
                    if root not in batches:
                        batches[root] = recycle.new_holding_batch(root)
                    held[path_str] = permissions.with_retry(
                        lambda: recycle.move_to_holding(norm_path, batches[root]), norm_path, unlock)
                except OSError as ex:
                    raise OSError("The Recycle Bin can't take this item and JunkZero could not set it aside "
                                  f"on this drive ({ex}); use Permanent delete to remove it") from ex
                logger.info(f"Held: {norm_path} -> {held[path_str]} ({format_size(size)})")

            deleted_keys.add(_key(norm_path))
            deleted_count += 1
            freed_bytes += size

        except Exception as ex:
            failed_count += 1
            err_msg = str(ex)
            error = {"path": path_str, "error": err_msg}
            # A permission problem: say why in plain words and what fixes it
            explained = None if isinstance(ex, TrashSetupError) else permissions.explain(ex, norm_path)
            if explained:
                error["kind"], why = explained
                error["error"] = f"Can't delete: {why}"
            errors.append(error)
            logger.error(f"Failed to delete {norm_path}: {err_msg}")

    flush()
    _set_progress(done=len(items) - skipped)
    return CleanResult(
        total_requested=len(items),
        deleted_count=deleted_count,
        failed_count=failed_count,
        freed_bytes=freed_bytes,
        freed_formatted=format_size(freed_bytes),
        mode=mode_str,
        errors=errors,
        held=held,
        trash_ids=trash_ids,
        started_at=started_at,
        skipped_count=skipped,
        cancelled=skipped > 0,
    )
