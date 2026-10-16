"""Permission problems when scanning or deleting: fix what JunkZero safely can, explain the rest.

JunkZero never uses administrator rights and never unlocks what the user locked. It only makes
the user's own files and folders writable so a delete they confirmed can finish (a read-only
file on Windows, a read-only folder on a Mac), and otherwise says why and how to fix it.
"""
from __future__ import annotations
import errno
import os
import stat
import sys
from typing import Callable, Optional, Tuple

from app.engine import osinfo

KIND_PRIVACY = "mac_privacy"        # macOS privacy settings kept JunkZero out
KIND_LOCKED = "locked"              # locked in Finder (or immutable)
KIND_NOT_YOURS = "not_yours"        # belongs to another account or the system
KIND_FOLDER = "folder_read_only"    # the folder holding it can't be changed
KIND_READ_ONLY_DISK = "read_only_disk"
KIND_DENIED = "access_denied"       # Windows "Access is denied" with no better reason

# System Settings > Privacy & Security > Full Disk Access
MAC_FULL_DISK_ACCESS_URL = "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles"

_WIN_ACCESS_DENIED = 5
_WIN_SHARING_VIOLATION = 32
_PERMISSION_WORDS = ("permission", "not permitted", "access is denied", "insufficient access privileges",
                     "not allowed", "operation not permitted")


def privacy_app() -> str:
    """The app macOS asks about: the packaged app, or the Terminal that run.command opens."""
    if getattr(sys, "frozen", False):
        return "JunkZero"
    return "Terminal (the app run.command opens JunkZero in)"


def mac_privacy_fix() -> str:
    return (f"Give {privacy_app()} Full Disk Access in System Settings > Privacy & Security > "
            "Full Disk Access, then restart JunkZero")


def _os_error(err: BaseException) -> Optional[OSError]:
    seen = 0
    while err is not None and seen < 5:
        if isinstance(err, OSError) and (err.errno or getattr(err, "winerror", None)):
            return err
        err = err.__cause__ or err.__context__
        seen += 1
    return None


def is_permission_error(err: BaseException) -> bool:
    """True for errors that mean "not allowed" (as opposed to missing, in use or full)."""
    os_err = _os_error(err)
    if os_err is not None:
        if getattr(os_err, "winerror", None) == _WIN_SHARING_VIOLATION:
            return False
        if os_err.errno in (errno.EPERM, errno.EACCES, errno.EROFS) or \
                getattr(os_err, "winerror", None) == _WIN_ACCESS_DENIED:
            return True
    text = str(err).lower()
    return any(word in text for word in _PERMISSION_WORDS)


def is_locked(path: str) -> bool:
    """Locked in Finder (user immutable flag) or immutable for everyone (system flag)."""
    try:
        flags = getattr(os.lstat(path), "st_flags", 0)
    except OSError:
        return False
    return bool(flags & (getattr(stat, "UF_IMMUTABLE", 0) | getattr(stat, "SF_IMMUTABLE", 0)))


def _owner_name(uid: int) -> str:
    try:
        import pwd
        return pwd.getpwuid(uid).pw_name
    except (ImportError, KeyError):
        return str(uid)


def _not_mine(path: str) -> Optional[str]:
    """The owner's name if path belongs to another account (POSIX), else None."""
    if not hasattr(os, "getuid"):
        return None
    try:
        uid = os.lstat(path).st_uid
    except OSError:
        return None
    return None if uid == os.getuid() else _owner_name(uid)


def explain(err: BaseException, path: str) -> Optional[Tuple[str, str]]:
    """(kind, what to tell the user) for a permission problem with path, or None if err is not one."""
    if not is_permission_error(err):
        return None
    os_err = _os_error(err)
    code = os_err.errno if os_err is not None else None
    parent = os.path.dirname(os.path.abspath(path))

    if code == errno.EROFS:
        return KIND_READ_ONLY_DISK, "the disk is read-only, so nothing on it can be deleted"
    if is_locked(path) or is_locked(parent):
        where = "it" if is_locked(path) else "the folder it is in"
        how = "select it in Finder, choose File > Get Info and untick Locked" if osinfo.is_macos() \
            else "remove the immutable flag"
        return KIND_LOCKED, f"{where} is locked: {how}, then try again"
    owner = _not_mine(path)
    if owner:
        return KIND_NOT_YOURS, (f"it belongs to {'the system' if owner == 'root' else 'the account ' + repr(owner)}, "
                                "and JunkZero doesn't use administrator rights")
    parent_owner = _not_mine(parent)
    if parent_owner or (hasattr(os, "access") and os.path.isdir(parent) and not os.access(parent, os.W_OK)):
        return KIND_FOLDER, f"you can't change the folder it is in ({parent})"
    if osinfo.is_macos():
        # Your own file, not locked, folder writable: macOS privacy settings blocked JunkZero
        return KIND_PRIVACY, f"macOS didn't allow JunkZero to change it. {mac_privacy_fix()}"
    if osinfo.is_windows():
        return KIND_DENIED, ("Windows denied access: it may belong to another account, be in use by a "
                             "system service, or need administrator rights, which JunkZero doesn't use")
    return KIND_DENIED, "you don't have permission to change it"


def make_writable(path: str) -> bool:
    """Make the user's own file or folder (and the folder holding it) writable so it can be removed.
    Never touches what belongs to someone else or what is locked. True if anything changed."""
    changed = False
    for target in (path, os.path.dirname(os.path.abspath(path))):
        if not os.path.lexists(target) or os.path.islink(target) or is_locked(target) or _not_mine(target):
            continue
        try:
            mode = os.lstat(target).st_mode
            if osinfo.is_windows():
                if not mode & stat.S_IWRITE:
                    os.chmod(target, stat.S_IWRITE | stat.S_IREAD)  # clears the read-only attribute
                    changed = True
            elif not mode & stat.S_IWUSR or (stat.S_ISDIR(mode) and not mode & stat.S_IXUSR):
                os.chmod(target, mode | stat.S_IWUSR | (stat.S_IXUSR if stat.S_ISDIR(mode) else 0))
                changed = True
        except OSError:
            continue
    return changed


def retry_writable(func: Callable, path: str, err: BaseException) -> None:
    """shutil.rmtree error handler: on a permission error, make the user's own item writable and try once more."""
    if is_permission_error(err) and make_writable(path):
        func(path)
        return
    raise err
